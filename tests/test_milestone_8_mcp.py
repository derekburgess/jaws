from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

from jaws.adapters.research_api import ResearchApplication, ResearchLimits
from jaws.research_cli import _dispatch
from jaws.services.research import ComponentDescriptor, ResearchCatalog
from jaws_mcp import server
from jaws_mcp.contracts import TOOL_CONTRACTS, contract_snapshot


def _catalog() -> ResearchCatalog:
    catalog = ResearchCatalog(
        datasets={"dataset-1"},
        captures={"capture-1"},
        labels={"truth:1"},
        prior_experiments={"prior-experiment"},
        benchmark_summaries=[{"benchmark_id": "benchmark-v1", "cells": 48}],
    )
    for descriptor in (
        ComponentDescriptor("representation", "numeric", "2"),
        ComponentDescriptor("reference", "peer", "3"),
        ComponentDescriptor("ranker", "demo", "4", compatible_representations=("numeric",)),
        ComponentDescriptor("evaluator", "metrics", "5"),
        ComponentDescriptor("renderer", "json", "6"),
    ):
        catalog.register(descriptor)
    return catalog


def _document() -> dict:
    ranking = [
        {"entity_id": "ip:198.51.100.20", "score": 2.0, "outlier": "outlier"},
        {"entity_id": "ip:192.0.2.10", "score": 1.0, "outlier": "inlier"},
    ]
    return {
        "schema_version": "1.0.0",
        "hypothesis": {
            "schema_version": "2.0.0",
            "claim": "treatment improves recall without increasing benign burden",
            "control": "control",
            "treatments": ["treatment"],
            "metrics": ["recall_at_2"],
            "metric_objectives": {"recall_at_2": "maximize", "benign_burden": "minimize"},
            "regression_budgets": {"benign_burden": 0.0},
        },
        "observation": {"capture_ids": ["capture-1"]},
        "entity": {"entity_type": "endpoint_ip", "version": "1"},
        "representation": {"representation_id": "numeric", "version": "2"},
        "reference": {"kind": "peer", "version": "3"},
        "ranker": {"ranker_id": "demo", "version": "4", "seed": 7},
        "evaluator": {"component_id": "metrics", "version": "5"},
        "renderer": {"component_id": "json", "version": "6"},
        "dataset_ids": ["dataset-1"],
        "evidence_digests": {"capture-1": "a" * 64, "dataset-1": "b" * 64},
        "label_source_versions": {"truth": "1"},
        "deterministic_settings": {
            "fixtures": {
                "control": {
                    "ranking": ranking,
                    "metrics": {"recall_at_2": 0.5, "benign_burden": 0.0},
                },
                "treatment": {
                    "ranking": list(reversed(ranking)),
                    "metrics": {"recall_at_2": 1.0, "benign_burden": 0.0},
                },
            }
        },
    }


def _terminal(application: ResearchApplication, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = application.experiment_status(job_id)["data"]
        if status["state"] in {"completed", "failed", "cancelled"}:
            return status
        time.sleep(0.01)
    raise AssertionError("experiment did not reach a terminal state")


def test_contract_snapshot_separates_policy_and_has_no_administration() -> None:
    snapshot = contract_snapshot()
    assert snapshot["schema_version"] == "2.0.0"
    assert snapshot["capability_version"] == "research-v2"
    assert tuple(snapshot["tools"]) == tuple(sorted(TOOL_CONTRACTS))
    assert {item["policy"] for item in TOOL_CONTRACTS.values()} == {"read", "mutation"}
    assert "administration" not in " ".join(TOOL_CONTRACTS).lower()
    assert "capture_packets" not in TOOL_CONTRACTS
    assert "database_delete" not in TOOL_CONTRACTS
    for contract in TOOL_CONTRACTS.values():
        assert contract["description"]
        assert contract["input_schema"]["additionalProperties"] is False


def test_thin_mcp_adapter_has_no_analytical_or_storage_bypass() -> None:
    source = Path(server.__file__).read_text(encoding="utf-8")
    forbidden = (
        "subprocess",
        "MATCH (",
        "Neo4j",
        "cypher",
        "DBSCAN",
        "sklearn",
        "feature engineering",
        "matplotlib",
        "docker.sock",
    )
    assert all(term not in source for term in forbidden)
    assert "ResearchApplication" in source
    assert "streamable-http" in source and 'transport="stdio"' in source


def test_orientation_scoped_operations_and_stable_errors(tmp_path: Path) -> None:
    calls: list[tuple[str, dict]] = []
    application = ResearchApplication(
        _catalog(),
        tmp_path,
        operations={
            "capture_profile": lambda resource, options: (
                calls.append((resource, dict(options))) or {"profiles": 2}
            )
        },
    )
    server.set_application(application)
    oriented = server.research_orient()
    assert oriented["ok"] is True
    assert oriented["data"]["catalog"]["datasets"] == ["dataset-1"]
    assert oriented["data"]["catalog"]["benchmark_summaries"][0]["cells"] == 48

    profiled = server.capture_profile("capture-1", "numeric", {"limit": 2})
    assert profiled["ok"] is True
    assert calls == [("capture-1", {"representation_id": "numeric", "limit": 2})]
    unavailable = server.capture_enrich("capture-1")
    assert unavailable["ok"] is False
    assert unavailable["error"]["code"] == "operation_unavailable"
    invalid = server.capture_profile("not-in-catalog", "numeric")
    assert invalid["error"]["code"] == "invalid_request"
    missing = server.experiment_status("job-missing")
    assert missing["error"]["code"] == "job_not_found"


def test_async_experiment_pagination_evidence_and_bundle_integrity(tmp_path: Path) -> None:
    inspections: list[tuple[int, int]] = []

    def inspect(_pointer, peer_limit, packet_limit):
        inspections.append((peer_limit, packet_limit))
        return {"peers": ["ip:192.0.2.10"][:peer_limit], "packets": [{"size": 82}][:packet_limit]}

    application = ResearchApplication(
        _catalog(),
        tmp_path,
        limits=ResearchLimits(max_page_size=2),
        inspection=inspect,
    )
    started = application.start_experiment(_document())
    assert started["ok"] is True
    job_id = started["data"]["job_id"]
    assert started["data"]["experiment_id"]
    assert _terminal(application, job_id)["state"] == "completed"

    first = application.experiment_result(job_id, offset=0, limit=2)
    second = application.experiment_result(job_id, offset=2, limit=2)
    assert first["ok"] and second["ok"]
    assert first["data"]["page"] == {"offset": 0, "limit": 2, "total": 4}
    assert len(first["data"]["findings"]) == len(second["data"]["findings"]) == 2
    pointer = first["data"]["findings"][0]["evidence"][0]
    inspected = application.inspect_evidence(pointer, peer_limit=1, packet_limit=1)
    assert inspected["data"]["resource_access"] == "scoped"
    assert inspected["data"]["evidence"]["capture_id"] == "capture-1"
    assert inspected["data"]["inspection"]["packets"] == [{"size": 82}]
    assert inspections == [(1, 1)]
    assert all(Path(item["bundle"]).is_dir() for item in first["data"]["runs"])


def test_secret_command_path_and_size_policy_are_rejected(tmp_path: Path) -> None:
    application = ResearchApplication(
        _catalog(), tmp_path, limits=ResearchLimits(max_request_bytes=20_000)
    )
    for key, value in (
        ("api_key", "secret"),
        ("command", "rm something"),
        ("host_path", "/etc/passwd"),
    ):
        document = _document()
        document["deterministic_settings"][key] = value
        response = server._call(lambda document=document: application.validate_experiment(document))
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_request"

    expensive = _document()
    expensive["deterministic_settings"]["fixtures"]["control"]["metrics"]["estimated_cost"] = 2.0
    response = server._call(lambda: application.validate_experiment(expensive))
    assert response["error"]["code"] == "limit_exceeded"


def test_empty_catalog_missing_component_and_insufficient_evidence_are_typed(
    tmp_path: Path,
) -> None:
    empty = ResearchApplication(ResearchCatalog(), tmp_path / "empty")
    invalid = server._call(lambda: empty.validate_experiment(_document()))
    assert invalid["error"]["code"] == "invalid_request"
    assert "unavailable experiment evidence" in invalid["error"]["message"]

    missing_model = _document()
    missing_model["ranker"]["ranker_id"] = "missing-model"
    invalid = server._call(
        lambda: ResearchApplication(_catalog(), tmp_path / "model").validate_experiment(
            missing_model
        )
    )
    assert invalid["error"]["code"] == "invalid_request"
    assert "unavailable versioned component" in invalid["error"]["message"]

    insufficient = _document()
    insufficient["deterministic_settings"]["fixtures"]["control"]["ranking"] = []
    invalid = server._call(
        lambda: ResearchApplication(_catalog(), tmp_path / "small").explore(
            insufficient, "control", "rank"
        )
    )
    assert invalid["error"]["code"] == "invalid_request"
    assert "nonempty array" in invalid["error"]["message"]


def test_cancellation_and_corrupt_artifact_are_distinct_terminal_outcomes(
    tmp_path: Path, monkeypatch
) -> None:
    from jaws.research_codec import DeclarativeResearchEngine

    entered = threading.Event()
    release = threading.Event()
    original = DeclarativeResearchEngine.represent

    def blocked(self, specification, variant):
        entered.set()
        assert release.wait(2)
        return original(self, specification, variant)

    monkeypatch.setattr(DeclarativeResearchEngine, "represent", blocked)
    cancelled_app = ResearchApplication(_catalog(), tmp_path / "cancel")
    started = cancelled_app.start_experiment(_document())
    job_id = started["data"]["job_id"]
    assert entered.wait(2)
    assert cancelled_app.cancel_experiment(job_id)["data"]["state"] == "cancelling"
    release.set()
    assert _terminal(cancelled_app, job_id)["state"] == "cancelled"

    monkeypatch.setattr(DeclarativeResearchEngine, "represent", original)
    corrupt_app = ResearchApplication(_catalog(), tmp_path / "corrupt")
    started = corrupt_app.start_experiment(_document())
    job_id = started["data"]["job_id"]
    assert _terminal(corrupt_app, job_id)["state"] == "completed"
    status_result = corrupt_app._job(job_id).result
    assert status_result is not None
    bundle = Path(status_result["runs"][0]["bundle"])
    ranking = next(bundle.glob("artifacts/*/ranking.json"))
    ranking.write_text("[]\n", encoding="utf-8")
    response = server._call(lambda: corrupt_app.experiment_result(job_id))
    assert response["error"]["code"] == "corrupt_artifact"


def test_cli_and_mcp_share_exact_exploratory_service_payload(tmp_path: Path) -> None:
    document = _document()
    spec_path = tmp_path / "experiment.json"
    spec_path.write_text(json.dumps(document), encoding="utf-8")
    cli, _ = _dispatch(
        argparse.Namespace(
            command="operation",
            spec=spec_path,
            variant="control",
            stage="rank",
        )
    )
    application = ResearchApplication(_catalog(), tmp_path / "mcp")
    mcp_payload = application.explore(document, "control", "rank")
    assert cli["stage"] == mcp_payload["data"]["stage"]
    assert cli["result"] == mcp_payload["data"]["result"]


class _TransportProbe:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_stdio_and_streamable_http_transports_are_selected_independently(monkeypatch) -> None:
    probe = _TransportProbe()
    monkeypatch.setattr(server, "mcp", probe)
    assert server.main(["--stdio"]) == 0
    assert server.main(["--http", "--host", "127.0.0.1", "--port", "9876"]) == 0
    assert probe.calls == [
        {"transport": "stdio"},
        {"transport": "streamable-http", "host": "127.0.0.1", "port": 9876},
    ]
