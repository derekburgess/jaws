from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from jaws.research_cli import main
from jaws.research_codec import decode_experiment_spec, experiment_document, load_document

REPOSITORY = Path(__file__).parents[1]
SPEC = REPOSITORY / "examples/research/control-treatment.json"
CATALOG = REPOSITORY / "examples/research/catalog.json"


def _invoke(capsys: object, arguments: list[str]) -> dict[str, object]:
    assert main([*arguments, "--json"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    value = json.loads(captured.out)
    assert isinstance(value, dict)
    return value


def test_sample_spec_round_trips_and_validates_against_schema() -> None:
    specification = decode_experiment_spec(load_document(SPEC))
    document = experiment_document(specification)
    assert decode_experiment_spec(document) == specification
    schema = load_document(REPOSITORY / "docs/schemas/research/experiment.schema.json")
    hypothesis_schema = load_document(REPOSITORY / "docs/schemas/research/hypothesis.schema.json")
    properties = dict(schema["properties"])
    properties["hypothesis"] = hypothesis_schema
    local_schema = {**schema, "properties": properties}
    jsonschema.Draft202012Validator(local_schema).validate(load_document(SPEC))


def test_research_cli_runs_inspects_compares_and_reports_status(
    tmp_path: Path, capsys: object
) -> None:
    root = tmp_path / "workbench"
    validated = _invoke(capsys, ["validate", str(SPEC), "--catalog", str(CATALOG)])
    assert validated["ok"] is True
    result = _invoke(
        capsys,
        ["run", str(SPEC), "--catalog", str(CATALOG), "--root", str(root)],
    )
    assert result["ok"] is True
    runs = result["runs"]
    assert isinstance(runs, list) and len(runs) == 2
    assert len(result["observations"]) == 1  # type: ignore[arg-type]
    assert len(result["report_bundles"]) == 1  # type: ignore[arg-type]

    control_bundle = Path(runs[0]["bundle"])
    treatment_bundle = Path(runs[1]["bundle"])
    control_id = runs[0]["run"]["run_id"]
    status = _invoke(capsys, ["status", control_id, "--root", str(root)])
    assert status["run"]["state"] == "completed"  # type: ignore[index]
    inspected = _invoke(capsys, ["inspect", str(control_bundle)])
    assert inspected["ok"] is True
    verified = _invoke(capsys, ["verify", str(treatment_bundle)])
    assert verified["ok"] is True
    compared = _invoke(capsys, ["compare", str(control_bundle), str(treatment_bundle)])
    deltas = compared["metric_deltas"]
    assert deltas["benign_burden"] == -1.0
    assert deltas["recall_at_3"] == 0.5
    assert deltas["runtime_seconds"] == pytest.approx(0.2)
    components = _invoke(capsys, ["components", "--catalog", str(CATALOG)])
    assert len(components["catalog"]["components"]) == 5  # type: ignore[index]

    schema_root = REPOSITORY / "docs/schemas/research"
    run_document = load_document(control_bundle / "run/run.json")
    jsonschema.Draft202012Validator(
        load_document(schema_root / "experiment-run.schema.json")
    ).validate(run_document)
    ranking = json.loads((control_bundle / "artifacts/control/ranking.json").read_text())
    finding_validator = jsonschema.Draft202012Validator(
        load_document(schema_root / "ranked-finding.schema.json")
    )
    for finding in ranking:
        finding_validator.validate(finding)
    jsonschema.Draft202012Validator(
        load_document(schema_root / "evaluation-result.schema.json")
    ).validate(load_document(control_bundle / "artifacts/control/evaluation.json"))
    report_bundle = Path(result["report_bundles"][0])  # type: ignore[index]
    jsonschema.Draft202012Validator(
        load_document(schema_root / "observation-report.schema.json")
    ).validate(load_document(report_bundle / "observation/report.json"))


def test_research_cli_individual_operation_and_portable_export(
    tmp_path: Path, capsys: object
) -> None:
    root = tmp_path / "workbench"
    operation = _invoke(capsys, ["operation", str(SPEC), "treatment", "rank"])
    assert operation["result"][0]["entity_id"] == "target.example"  # type: ignore[index]
    result = _invoke(
        capsys,
        ["run", str(SPEC), "--catalog", str(CATALOG), "--root", str(root)],
    )
    runs = result["runs"]
    bundle = Path(runs[0]["bundle"])  # type: ignore[index]
    archive = tmp_path / "portable.tar.gz"
    exported = _invoke(
        capsys,
        [
            "export",
            str(bundle),
            str(archive),
            "--root",
            str(root / "bundles"),
        ],
    )
    assert exported["ok"] is True and archive.is_file()
    imported = _invoke(
        capsys,
        ["import", str(archive), "--root", str(tmp_path / "imported")],
    )
    assert Path(str(imported["bundle"])).is_dir()
