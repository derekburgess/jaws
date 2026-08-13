"""Explicit plan/apply CLI for declared JAWS retention policies."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from typing import cast

from jaws.adapters import SystemClock, UuidAuditEventIdGenerator
from jaws.domain import AuditContext, RetentionPolicy, primitive
from jaws.services import RetentionService
from jaws.storage import Neo4jRepositories


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jaws-retention",
        description="Dry-run or apply an explicit JAWS evidence-retention policy.",
    )
    parser.add_argument("operation", choices=("dry-run", "apply"))
    parser.add_argument(
        "--retain-profiles",
        type=int,
        default=20,
        help="Keep the latest N computed profile sets; 0 keeps all profile sets.",
    )
    parser.add_argument("--database", help="Neo4j database name; defaults to JAWS settings")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = importlib.import_module("jaws.config")
    database = args.database or cast(str, config.DATABASE)
    try:
        policy = RetentionPolicy.legacy_profile_limit(args.retain_profiles)
        repositories = Neo4jRepositories.connect(config.get_neo4j_driver(), database)
        service = RetentionService(
            repositories.profiles,
            SystemClock(),
            UuidAuditEventIdGenerator(),
            AuditContext("local_operator", "jaws-retention", database),
        )
        result = (
            service.dry_run(policy)
            if args.operation == "dry-run"
            else service.apply(service.plan(policy))
        )
        print(json.dumps(primitive(result), sort_keys=True, indent=2))
        return 0
    except (RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
