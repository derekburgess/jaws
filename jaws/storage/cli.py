"""Administrative CLI for versioned Neo4j schema operations."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from jaws.adapters.migration_backup import verify_migration_backup
from jaws.domain import primitive

from .migrations import MigrationError, manager
from .neo4j_evidence_repository import Neo4jEvidenceRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jaws-schema",
        description="Inspect, validate, plan, or migrate the JAWS Neo4j schema.",
    )
    parser.add_argument("operation", choices=("status", "validate", "dry-run", "migrate"))
    parser.add_argument("--database", help="Neo4j database name; defaults to JAWS settings")
    parser.add_argument(
        "--backup",
        type=Path,
        help="Verified evidence bundle required by a destructive or irreversible migration",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.backup is not None and args.operation != "migrate":
        _parser().error("--backup is valid only with the migrate operation")

    # Importing the CLI module remains credential- and driver-free. Configuration and
    # the optional Neo4j dependency are resolved only when an operation is invoked.
    config = importlib.import_module("jaws.config")
    database = args.database or cast(str, config.DATABASE)
    try:
        driver = config.get_neo4j_driver()
        migration_manager = manager(driver, database)
        if args.operation == "status":
            output: object = migration_manager.status()
            exit_code = 0
        elif args.operation == "validate":
            status = migration_manager.validate()
            output = status
            exit_code = 0 if status.is_current else 1
        elif args.operation == "dry-run":
            output = migration_manager.dry_run()
            exit_code = 0
        else:
            plan = migration_manager.dry_run()
            backup = None
            if plan.backup_required:
                if args.backup is None:
                    versions = ", ".join(str(version) for version in plan.backup_required_versions)
                    raise MigrationError(
                        "verified evidence backup required; pass --backup for migration "
                        f"version(s): {versions}"
                    )
                backup = verify_migration_backup(
                    args.backup,
                    database=database,
                    plan=plan,
                    evidence=Neo4jEvidenceRepository(driver, database),
                )
            output = migration_manager.migrate(backup)
            exit_code = 0
        print(json.dumps(primitive(output), sort_keys=True, indent=2))
        return exit_code
    except (MigrationError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
