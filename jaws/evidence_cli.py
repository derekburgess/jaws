"""Explicit export, validation, and empty-target import for JAWS evidence bundles."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from jaws.adapters import (
    EvidenceBundleError,
    SystemClock,
    load_evidence_bundle,
    write_evidence_bundle,
)
from jaws.domain import primitive
from jaws.services import EvidenceTransferService
from jaws.storage import Neo4jEvidenceRepository, Neo4jRepositories


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jaws-evidence",
        description="Export, validate, or import a checksummed JAWS evidence bundle.",
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    export = subparsers.add_parser("export", help="Atomically create a new evidence bundle")
    export.add_argument("path", type=Path)
    export.add_argument("--database", help="Neo4j database name; defaults to JAWS settings")
    validate = subparsers.add_parser("validate", help="Verify a bundle without Neo4j access")
    validate.add_argument("path", type=Path)
    restore = subparsers.add_parser(
        "import", help="Restore a bundle into an empty matching managed database"
    )
    restore.add_argument("path", type=Path)
    restore.add_argument("--database", help="Neo4j database name; defaults to JAWS settings")
    restore.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate schema and target emptiness without writing",
    )
    return parser


def _repositories(database_override: str | None) -> Neo4jRepositories:
    config = importlib.import_module("jaws.config")
    database = database_override or cast(str, config.DATABASE)
    return Neo4jRepositories.connect(config.get_neo4j_driver(), database)


def _export_repository(database_override: str | None) -> Neo4jEvidenceRepository:
    """Permit a safe export from a valid applied prefix before pending migrations."""

    config = importlib.import_module("jaws.config")
    database = database_override or cast(str, config.DATABASE)
    return Neo4jEvidenceRepository(config.get_neo4j_driver(), database)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "validate":
            bundle = load_evidence_bundle(args.path)
            print(
                json.dumps(
                    {"manifest": primitive(bundle.manifest), "valid": True},
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0
        if args.operation == "export":
            service = EvidenceTransferService(_export_repository(args.database), SystemClock())
            bundle = service.export()
            write_evidence_bundle(args.path, bundle)
            output: object = {"manifest": primitive(bundle.manifest), "path": str(args.path)}
        else:
            repositories = _repositories(args.database)
            service = EvidenceTransferService(repositories.evidence, SystemClock())
            bundle = load_evidence_bundle(args.path)
            plan = service.plan_import(bundle)
            output = (
                service.dry_run_import(bundle)
                if args.dry_run
                else service.apply_import(bundle, plan)
            )
        print(json.dumps(primitive(output), sort_keys=True, indent=2))
        return 0
    except (EvidenceBundleError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
