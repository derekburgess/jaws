"""Human-only guarded administration for managed JAWS evidence databases."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence

from jaws.adapters import SystemClock, UuidAuditEventIdGenerator
from jaws.domain import AuditContext, primitive
from jaws.services import AdministrationService
from jaws.storage import Neo4jRepositories


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jaws-admin",
        description=(
            "Plan, explicitly confirm, and audit destructive operations. "
            "This interface is intentionally not agent-accessible."
        ),
    )
    parser.add_argument("operation", choices=("plan", "erase", "audit"))
    parser.add_argument(
        "--database",
        required=True,
        help="Exact Neo4j database name; destructive operations have no default target.",
    )
    parser.add_argument(
        "--confirm",
        help="For erase, the exact confirmation string emitted by a current plan.",
    )
    parser.add_argument("--limit", type=int, default=100, help="Audit records to return.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.operation == "erase" and args.confirm is None:
        print(
            json.dumps(
                {
                    "error": "erase requires --confirm with the exact value from jaws-admin plan",
                    "ok": False,
                }
            ),
            file=sys.stderr,
        )
        return 2
    config = importlib.import_module("jaws.config")
    driver = None
    try:
        driver = config.get_neo4j_driver()
        repositories = Neo4jRepositories.connect(driver, args.database)
        service = AdministrationService(
            repositories.administration,
            SystemClock(),
            UuidAuditEventIdGenerator(),
            AuditContext("local_operator", "jaws-admin", args.database),
        )
        if args.operation == "plan":
            plan = service.plan()
            output: object = {
                **primitive(plan),
                "digest": str(plan.digest),
                "confirmation": plan.confirmation,
            }
        elif args.operation == "erase":
            plan = service.plan()
            output = service.erase(plan, args.confirm)
        else:
            output = service.audit_events(limit=args.limit)
        print(json.dumps({"ok": True, "result": primitive(output)}, sort_keys=True, indent=2))
        return 0
    except (RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    raise SystemExit(main())
