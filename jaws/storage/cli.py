"""Administrative CLI for versioned Neo4j schema operations."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from typing import cast

from jaws.domain import primitive

from .migrations import MigrationError, manager


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jaws-schema",
        description="Inspect, validate, plan, or migrate the JAWS Neo4j schema.",
    )
    parser.add_argument("operation", choices=("status", "validate", "dry-run", "migrate"))
    parser.add_argument("--database", help="Neo4j database name; defaults to JAWS settings")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Importing the CLI module remains credential- and driver-free. Configuration and
    # the optional Neo4j dependency are resolved only when an operation is invoked.
    config = importlib.import_module("jaws.config")
    database = args.database or cast(str, config.DATABASE)
    try:
        migration_manager = manager(config.get_neo4j_driver(), database)
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
            output = migration_manager.migrate()
            exit_code = 0
        print(json.dumps(primitive(output), sort_keys=True, indent=2))
        return exit_code
    except (MigrationError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
