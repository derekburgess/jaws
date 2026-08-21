"""Run the dependency-free scripted OHEO laboratory spike."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from jaws.adapters.research_api import ResearchApplication
from jaws.domain import canonical_json
from jaws.research_codec import decode_experiment_spec, load_catalog, load_document

from .contracts import redacted_lab_document
from .orchestrator import ApprovalRequired, InHouseOHEO
from .scripted import ScriptedResearchModel


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaws-lab")
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("identity")
    identity.add_argument("experiment", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--catalog", type=Path, required=True)
    run.add_argument("--experiment", type=Path, required=True)
    run.add_argument("--root", type=Path, default=Path(".jaws-lab"))
    run.add_argument("--confirmation", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    document = load_document(args.experiment)
    specification = decode_experiment_spec(document)
    if args.command == "identity":
        print(specification.experiment_id.value)
        return 0
    application = ResearchApplication(load_catalog(args.catalog), args.root / "research")
    orchestrator = InHouseOHEO(application)
    try:
        result = orchestrator.run(
            ScriptedResearchModel(document), confirmations=tuple(args.confirmation)
        )
    except ApprovalRequired as error:
        print(
            canonical_json(
                {"ok": False, "error": "approval_required", "expected": error.expected_confirmation}
            )
        )
        return 3
    trace_path = args.root / "traces" / f"{result.job_id}.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        canonical_json(redacted_lab_document(result.trace)) + "\n", encoding="utf-8"
    )
    print(
        canonical_json(
            {"ok": True, "result": redacted_lab_document(result), "trace": str(trace_path)}
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
