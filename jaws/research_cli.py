"""Research experiment CLI with stable JSON automation output."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jaws.adapters.experiment_bundles import ExperimentBundleStore
from jaws.adapters.provenance import ProvenanceCollector
from jaws.adapters.run_journal import RunJournal
from jaws.domain import RunId, canonical_json, primitive
from jaws.research_codec import (
    DeclarativeResearchEngine,
    decode_experiment_spec,
    experiment_document,
    load_catalog,
    load_document,
)
from jaws.services.research import (
    ExperimentService,
    ObserveService,
    ResearchRunRepository,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaws-research")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate an immutable experiment spec")
    validate.add_argument("spec", type=Path)
    validate.add_argument("--catalog", type=Path, required=True)
    _json_option(validate)

    run = commands.add_parser("run", help="execute the declared control/treatment matrix")
    run.add_argument("spec", type=Path)
    run.add_argument("--catalog", type=Path, required=True)
    run.add_argument("--root", type=Path, default=Path(".jaws-research"))
    _json_option(run)

    status = commands.add_parser("status", help="read an atomic run lifecycle snapshot")
    status.add_argument("run_id")
    status.add_argument("--root", type=Path, default=Path(".jaws-research"))
    _json_option(status)

    cancel = commands.add_parser("cancel", help="request cooperative run cancellation")
    cancel.add_argument("run_id")
    cancel.add_argument("--root", type=Path, default=Path(".jaws-research"))
    _json_option(cancel)

    inspect = commands.add_parser("inspect", help="inspect a run bundle without Neo4j")
    inspect.add_argument("bundle", type=Path)
    _json_option(inspect)

    compare = commands.add_parser("compare", help="compare analytical run artifacts")
    compare.add_argument("control", type=Path)
    compare.add_argument("treatment", type=Path)
    _json_option(compare)

    verify = commands.add_parser("verify", help="verify every bundle checksum")
    verify.add_argument("bundle", type=Path)
    _json_option(verify)

    components = commands.add_parser("components", help="list available versioned components")
    components.add_argument("--catalog", type=Path, required=True)
    _json_option(components)

    operation = commands.add_parser(
        "operation", help="run one deterministic operation outside a formal experiment"
    )
    operation.add_argument("spec", type=Path)
    operation.add_argument("variant")
    operation.add_argument("stage", choices=("represent", "reference", "rank", "evaluate"))
    _json_option(operation)

    export = commands.add_parser("export", help="export a portable bundle without raw PCAP")
    export.add_argument("bundle", type=Path)
    export.add_argument("archive", type=Path)
    export.add_argument("--root", type=Path, default=Path(".jaws-research/bundles"))
    _json_option(export)

    import_command = commands.add_parser("import", help="import and verify a portable bundle")
    import_command.add_argument("archive", type=Path)
    import_command.add_argument("--root", type=Path, default=Path(".jaws-research/bundles"))
    _json_option(import_command)
    return parser


def _json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", dest="json_output")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        payload, summary = _dispatch(arguments)
        _emit(payload, summary, arguments.json_output)
        return 0
    except (KeyError, OSError, TypeError, ValueError) as error:
        payload = {"ok": False, "error": type(error).__name__, "message": str(error)}
        _emit(payload, f"Error: {error}", arguments.json_output)
        return 2


def _dispatch(arguments: argparse.Namespace) -> tuple[object, str]:
    if arguments.command == "validate":
        specification = decode_experiment_spec(load_document(arguments.spec))
        load_catalog(arguments.catalog).validate(specification)
        payload = {"ok": True, "specification": experiment_document(specification)}
        return payload, f"Valid experiment {specification.experiment_id.value}"
    if arguments.command == "run":
        specification = decode_experiment_spec(load_document(arguments.spec))
        catalog = load_catalog(arguments.catalog)
        root: Path = arguments.root
        journal = RunJournal(root / "journal")
        bundles = ExperimentBundleStore(root / "bundles")
        service = ExperimentService(
            catalog,
            ResearchRunRepository(journal.write),
            bundles,
            ProvenanceCollector(Path.cwd()),
            cancellation_factory=journal.cancellation_signal,
        )
        executions = service.execute_matrix(specification, DeclarativeResearchEngine())
        observations: list[Mapping[str, Any]] = []
        report_bundles: list[str] = []
        if len(executions) > 1 and executions[0].evaluation is not None:
            for treatment in executions[1:]:
                if treatment.evaluation is None:
                    continue
                report = ObserveService().observe(
                    specification, executions[0].evaluation, treatment.evaluation
                )
                path, _ = bundles.write_observation(
                    specification,
                    report,
                    {"control": executions[0].evaluation, treatment.variant: treatment.evaluation},
                )
                observations.append(primitive(report))
                report_bundles.append(str(path))
        payload = {
            "ok": all(item.run.state.value == "completed" for item in executions),
            "experiment_id": specification.experiment_id.value,
            "runs": primitive(executions),
            "observations": observations,
            "report_bundles": report_bundles,
        }
        return payload, f"Executed {len(executions)} run(s) for {specification.experiment_id.value}"
    if arguments.command == "status":
        snapshot = RunJournal(arguments.root / "journal").read(RunId(arguments.run_id))
        if snapshot is None:
            raise KeyError(f"run not found: {arguments.run_id}")
        return {"ok": True, "run": snapshot}, f"Run {arguments.run_id}: {snapshot['state']}"
    if arguments.command == "cancel":
        marker = RunJournal(arguments.root / "journal").request_cancellation(
            RunId(arguments.run_id)
        )
        return {"ok": True, "run_id": arguments.run_id}, f"Cancellation requested: {marker}"
    if arguments.command == "inspect":
        store = ExperimentBundleStore(arguments.bundle.parent)
        verification = store.verify(arguments.bundle)
        manifest = load_document(arguments.bundle / "manifest.json")
        payload = {
            "ok": verification.valid,
            "manifest": manifest,
            "verification": primitive(verification),
        }
        return (
            payload,
            f"Bundle {manifest.get('run_id', manifest.get('observation_id'))}: {'valid' if verification.valid else 'invalid'}",
        )
    if arguments.command == "compare":
        compare_payload = _compare_bundles(arguments.control, arguments.treatment)
        return compare_payload, (
            f"Rankings {'match' if compare_payload['ranking_identical'] else 'differ'}"
        )
    if arguments.command == "verify":
        verification = ExperimentBundleStore(arguments.bundle.parent).verify(arguments.bundle)
        return {"ok": verification.valid, "verification": primitive(verification)}, (
            "Bundle checksums valid" if verification.valid else "Bundle verification failed"
        )
    if arguments.command == "components":
        catalog_snapshot = load_catalog(arguments.catalog).snapshot()
        return {"ok": True, "catalog": primitive(catalog_snapshot)}, (
            f"{len(catalog_snapshot.components)} component(s)"
        )
    if arguments.command == "operation":
        specification = decode_experiment_spec(load_document(arguments.spec))
        engine = DeclarativeResearchEngine()
        representation = engine.represent(specification, arguments.variant)
        if arguments.stage == "represent":
            result = representation
        else:
            reference = engine.reference(specification, arguments.variant, representation)
            if arguments.stage == "reference":
                result = reference
            else:
                findings = engine.rank(specification, arguments.variant, representation, reference)
                if arguments.stage == "rank":
                    result = findings
                else:
                    result = engine.evaluate(
                        specification, arguments.variant, RunId("exploratory"), findings
                    )
        return {
            "ok": True,
            "stage": arguments.stage,
            "result": primitive(result),
        }, f"Completed {arguments.stage}"
    if arguments.command == "export":
        digest = ExperimentBundleStore(arguments.root).export_bundle(
            arguments.bundle, arguments.archive
        )
        return {
            "ok": True,
            "archive": str(arguments.archive),
            "digest": str(digest),
        }, f"Exported {arguments.archive}"
    if arguments.command == "import":
        path = ExperimentBundleStore(arguments.root).import_bundle(arguments.archive)
        return {"ok": True, "bundle": str(path)}, f"Imported {path}"
    raise ValueError(f"unsupported command: {arguments.command}")


def _compare_bundles(control: Path, treatment: Path) -> Mapping[str, Any]:
    left = _evaluation_document(control)
    right = _evaluation_document(treatment)
    left_metrics = _numeric_mapping(left.get("metrics"))
    right_metrics = _numeric_mapping(right.get("metrics"))
    return {
        "ok": True,
        "metric_deltas": {
            key: right_metrics.get(key, 0.0) - left_metrics.get(key, 0.0)
            for key in sorted(set(left_metrics) | set(right_metrics))
        },
        "ranking_identical": left.get("ranked_entity_ids") == right.get("ranked_entity_ids"),
        "findings_digest_identical": left.get("findings_digest") == right.get("findings_digest"),
    }


def _evaluation_document(bundle: Path) -> Mapping[str, Any]:
    candidates = tuple(bundle.glob("artifacts/*/evaluation.json"))
    if len(candidates) != 1:
        raise ValueError(f"bundle must contain exactly one evaluation: {bundle}")
    return load_document(candidates[0])


def _numeric_mapping(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError("evaluation metrics must be an object")
    return {str(key): float(item) for key, item in value.items()}


def _emit(payload: object, summary: str, json_output: bool) -> None:
    if json_output:
        sys.stdout.write(canonical_json(payload) + "\n")
    else:
        sys.stdout.write(summary + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
