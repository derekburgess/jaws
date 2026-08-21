"""Integrated release-version, packaging, reference, and architecture contracts."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = "3.0.0"


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for source in path.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".", 1)[0])
    return modules


def test_major_version_is_synchronized_across_package_and_runtime_profiles() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == PACKAGE_VERSION
    for path in (
        ROOT / "compose.dev.yml",
        ROOT / "compose.gpu.yml",
        ROOT / "compose.edge.yml",
        ROOT / "compose.agent.yml",
        ROOT / "containers" / "Dockerfile",
        ROOT / "containers" / "Dockerfile.gpu",
        ROOT / "containers" / "Dockerfile.agent",
    ):
        text = path.read_text(encoding="utf-8")
        assert "2.0.0" not in text
        assert PACKAGE_VERSION in text


def test_every_wheel_builder_copies_every_declared_distribution_package() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = set(pyproject["tool"]["setuptools"]["packages"])
    top_level = {name.split(".", 1)[0] for name in packages}
    assert top_level == {"jaws", "jaws_mcp", "jaws_lab"}
    for name in ("Dockerfile", "Dockerfile.gpu", "Dockerfile.agent"):
        text = (ROOT / "containers" / name).read_text(encoding="utf-8")
        for package in top_level:
            assert f"COPY {package} ./{package}" in text


def test_interface_adapters_cannot_bypass_service_or_storage_boundaries() -> None:
    mcp_imports = _imports(ROOT / "jaws_mcp")
    lab_imports = _imports(ROOT / "jaws_lab")
    core_imports = _imports(ROOT / "jaws" / "services") | _imports(ROOT / "jaws" / "domain")
    assert not {"neo4j", "subprocess"} & mcp_imports
    assert not {"neo4j", "subprocess"} & lab_imports
    assert not {"jaws_mcp", "jaws_lab"} & core_imports

    mcp_source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "jaws_mcp").rglob("*.py")
    )
    assert "MATCH (" not in mcp_source
    assert "DETACH DELETE" not in mcp_source


def test_generated_reference_is_reproducible_and_covers_every_mcp_tool(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_reference.py"),
            "--output-root",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cli.md").read_bytes() == (
        ROOT / "docs" / "reference" / "cli.md"
    ).read_bytes()
    assert (tmp_path / "mcp-v2.md").read_bytes() == (
        ROOT / "docs" / "reference" / "mcp-v2.md"
    ).read_bytes()
    contract = json.loads(
        (ROOT / "jaws_mcp" / "contracts" / "v2" / "tool-contracts.json").read_text(encoding="utf-8")
    )
    reference = (ROOT / "docs" / "reference" / "mcp-v2.md").read_text(encoding="utf-8")
    assert len(contract["tools"]) == 13
    assert all(f"`{name}`" in reference for name in contract["tools"])


def test_release_documentation_names_workflow_migration_limits_and_review_gate() -> None:
    required = {
        "research-workflow.md": "Orient → Hypothesize → Experiment → Observe",
        "migration-2-to-3.md": "manages schema version\n7",
        "limitations.md": "Anomaly is not threat",
        "release-notes-3.0.0-rc1.md": "not a final publication",
        "adr/0021-major-release-and-experimental-agent-lab.md": "3.0.0",
    }
    for relative, phrase in required.items():
        text = (ROOT / "docs" / relative).read_text(encoding="utf-8")
        assert phrase in text
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "JAWS 3.0" in readme
    assert "docs/research-workflow.md" in readme
    assert "docs/migration-2-to-3.md" in readme
    assert "docs/limitations.md" in readme


def test_release_candidate_record_and_benchmark_asset_are_self_verifying() -> None:
    release = ROOT / "release" / "3.0.0-rc1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_qualify.py"), str(release)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verified_files"] == 6

    qualification = json.loads((release / "qualification.json").read_text(encoding="utf-8"))
    revision = qualification["source_revision"]
    assert len(revision) == 40
    assert qualification["gates"]["main_merge"] == "review-gated"
    assert qualification["gates"]["final_release_tag"] == "review-gated"
    assert qualification["tests"]["synthetic_recall"]["known_failures"] == 3

    images = json.loads((release / "images.json").read_text(encoding="utf-8"))["images"]
    for name in ("analyzer", "sensor", "mcp", "agent_lab"):
        assert images[name]["package_version"] == PACKAGE_VERSION
        assert images[name]["source_revision"] == revision
        assert images[name]["digest"].startswith("sha256:")

    risks = json.loads((release / "known-risks.json").read_text(encoding="utf-8"))
    assert risks["accepted_regressions"] == []
    assert {item["id"] for item in risks["known_quality_failures"]} == {
        "BF0-KF-001",
        "BF0-KF-002",
        "BF0-KF-003",
    }

    manifest = json.loads((release / "benchmark-manifest.json").read_text(encoding="utf-8"))
    archive = release / manifest["archive"]["name"]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive"]["sha256"]
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        files = [member for member in members if member.isfile()]
        assert len(files) == manifest["archive"]["expanded_file_count"]
        assert all(not Path(member.name).is_absolute() for member in members)
        assert all(".." not in Path(member.name).parts for member in members)
        assert all(not member.name.endswith((".pcap", ".pcapng")) for member in members)
        for name, digest in manifest["report_checksums"].items():
            member = bundle.extractfile(f"jaws-3.0.0-rc1-benchmark/{name}")
            assert member is not None
            assert hashlib.sha256(member.read()).hexdigest() == digest
