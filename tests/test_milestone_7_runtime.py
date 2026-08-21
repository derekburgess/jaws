from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from jaws.adapters.provenance import ProvenanceCollector
from jaws.domain import (
    CaptureId,
    ComponentSpec,
    ExperimentSpec,
    ObservationWindow,
    RankerSpec,
    ReferenceSpec,
    RepresentationSpec,
)
from jaws.runtime_cli import health, inventory, pcap_smoke, safe_fixture_bytes, verify_artifacts

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _yaml(name: str) -> dict:
    document = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _spec() -> ExperimentSpec:
    return ExperimentSpec(
        hypothesis="Runtime provenance keeps output-affecting image and model identity",
        observation=ObservationWindow(capture_ids=(CaptureId("runtime-fixture"),)),
        representation=RepresentationSpec(representation_id="numeric"),
        reference=ReferenceSpec(),
        ranker=RankerSpec(ranker_id="numeric_current", seed=7),
        evaluator=ComponentSpec(component_id="reward_vector"),
        renderer=ComponentSpec(component_id="json"),
    )


def test_image_lock_and_dockerfiles_are_pinned_source_builds_without_secrets() -> None:
    lock = json.loads((ROOT / "containers" / "images.lock.json").read_text())
    assert lock["schema_version"] == "1.0.0"
    assert set(lock["images"]) == {"python_cpu", "neo4j", "nvidia_cuda"}
    assert all(DIGEST.fullmatch(item["digest"]) for item in lock["images"].values())

    paths = (
        ROOT / "containers" / "Dockerfile",
        ROOT / "containers" / "Dockerfile.gpu",
        ROOT / "containers" / "Dockerfile.agent",
    )
    assert not (ROOT / "harbor" / "Dockerfile").exists()
    assert not (ROOT / "ocean" / "Dockerfile").exists()
    combined = "\n".join(path.read_text() for path in paths)
    lowered = combined.lower()
    assert "git clone" not in lowered
    assert 'tail", "-f"' not in lowered
    assert "arg neo4j_password" not in lowered
    assert "arg openai_api_key" not in lowered
    assert "arg ipinfo_api_key" not in lowered
    assert "docker.sock" not in lowered
    assert combined.count("@sha256:") >= 3
    assert "COPY jaws ./jaws" in combined
    assert "JAWS_BENCHMARK_ROOT=/workspace/benchmarks/v1" in combined
    assert "org.opencontainers.image.revision" in combined
    assert "USER 10001:10001" in combined


def test_compose_profiles_enforce_health_migration_persistence_and_least_privilege() -> None:
    development = _yaml("compose.dev.yml")
    gpu = _yaml("compose.gpu.yml")
    edge = _yaml("compose.edge.yml")
    services = development["services"]
    assert services["neo4j"]["image"].endswith(
        "@sha256:f0a9509090d06027749f647314bae5a549f95192b4ac7080104226e448fd7793"
    )
    assert services["migrate"]["depends_on"]["neo4j"]["condition"] == "service_healthy"
    assert services["analyzer"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert services["mcp"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )
    assert {"neo4j-data", "artifacts", "model-cache"} <= set(development["volumes"])
    assert development["networks"]["jaws-core"]["internal"] is True
    for name in ("analyzer", "mcp", "migrate"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert service.get("network_mode") != "host"
    assert all("docker.sock" not in str(service) for service in services.values())
    assert edge["services"]["sensor"]["cap_add"] == ["NET_RAW", "NET_ADMIN"]
    assert edge["services"]["sensor"]["cap_drop"] == ["ALL"]
    assert edge["services"]["analyzer"]["volumes"][1].endswith(":ro")
    assert gpu["services"]["gpu-analyzer"]["deploy"]["resources"]["reservations"]["devices"][0][
        "capabilities"
    ] == ["gpu"]


def test_runtime_health_inventory_and_payload_free_pcap_smoke(tmp_path: Path) -> None:
    fixture = safe_fixture_bytes()
    assert len(fixture) == 82
    assert fixture[:4] == bytes.fromhex("d4c3b2a1")
    assert bytes.fromhex("c0000201") in fixture
    assert bytes.fromhex("c6336402") in fixture
    output = tmp_path / "safe.pcap"
    result = pcap_smoke(output, require_tshark=False)
    assert result["ok"] and result["live_capture"] is False
    assert output.read_bytes() == fixture
    assert health("analyzer")["ok"] is True
    document = inventory()
    assert document["schema_version"] == "1.0.0"
    assert "packages" in document
    assert not ({"NEO4J_PASSWORD", "OPENAI_API_KEY", "IPINFO_API_KEY"} & set(document))


def test_container_and_model_digests_enter_allowlisted_run_provenance(monkeypatch) -> None:
    monkeypatch.setenv("JAWS_CONTAINER_DIGEST", "a" * 64)
    monkeypatch.setenv("JAWS_MODEL_DIGEST", "b" * 64)
    provenance = ProvenanceCollector(ROOT).collect(_spec())
    assert tuple(str(item) for item in provenance.container_digests) == ("a" * 64,)
    ranker = next(item for item in provenance.strategies if item.kind == "ranker")
    assert ranker.model_digest is not None and str(ranker.model_digest) == "b" * 64


def test_runtime_cli_contracts_execute_without_optional_services(tmp_path: Path) -> None:
    inventory_path = tmp_path / "inventory.json"
    inventory_run = subprocess.run(
        [sys.executable, "-m", "jaws.runtime_cli", "inventory", "--output", str(inventory_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert inventory_run.returncode == 0, inventory_run.stderr
    assert json.loads(inventory_path.read_text())["schema_version"] == "1.0.0"
    pcap_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "jaws.runtime_cli",
            "pcap-smoke",
            "--output",
            str(tmp_path / "fixture.pcap"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert pcap_run.returncode == 0, pcap_run.stderr
    assert json.loads(pcap_run.stdout)["live_capture"] is False
    try:
        verify_artifacts(tmp_path / "missing-bundles")
    except RuntimeError as error:
        assert "no experiment run bundles" in str(error)
    else:
        raise AssertionError("empty artifact roots must not verify")


def test_runtime_operations_document_every_volume_and_egress_boundary() -> None:
    operations = (ROOT / "docs" / "runtime-operations.md").read_text()
    for term in (
        "neo4j-data",
        "artifacts",
        "raw-evidence",
        "model-cache",
        "backup",
        "restore",
        "export",
        "import",
        "benchmark",
        "IP enrichment",
        "Remote embeddings",
        "Agent execution",
    ):
        assert term in operations
    assert "--volumes" in operations
    assert "Docker-socket mounts are prohibited" in operations
