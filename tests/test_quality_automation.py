"""Quality-tool, workflow, cache-policy, and benchmark-report contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
INTEGRATION_PATH = REPO_ROOT / ".github" / "workflows" / "integration.yml"
EXPECTED_SCENARIOS = {
    "behavioral_change",
    "bulk_download",
    "burst_exfil",
    "jittered_beacon",
    "payload_beacon",
    "slow_exfil",
    "stable_heavy",
    "tcp_keepalive",
}


def _names(requirements: list[str]) -> set[str]:
    return {Requirement(value).name.lower() for value in requirements}


def _workflow(path: Path) -> dict:
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def _steps_with_action(workflow: dict, action: str) -> list[dict]:
    return [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == action
    ]


def test_quality_tools_are_declared_and_exactly_constrained():
    dev_dependencies = _names(PYPROJECT["project"]["optional-dependencies"]["dev"])
    assert {"mypy", "pyyaml", "ruff"} <= dev_dependencies

    constraints = {}
    for raw in (
        (REPO_ROOT / "constraints" / "py312-direct.txt").read_text(encoding="utf-8").splitlines()
    ):
        value = raw.strip()
        if value and not value.startswith("#"):
            requirement = Requirement(value)
            constraints[requirement.name.lower()] = requirement

    for name in ("mypy", "pyyaml", "ruff"):
        specs = list(constraints[name].specifier)
        assert len(specs) == 1 and specs[0].operator == "=="


def test_lint_and_type_boundaries_are_explicit_ratchets():
    ruff = PYPROJECT["tool"]["ruff"]
    assert ruff["target-version"] == "py312"
    assert set(ruff["lint"]["select"]) == {"E4", "E7", "E9", "F", "I"}

    mypy = PYPROJECT["tool"]["mypy"]
    assert mypy["strict"] is True
    assert mypy["warn_unused_configs"] is True
    assert set(mypy["files"]) == {
        "jaws/adapters/",
        "jaws/domain/",
        "jaws/ports/",
        "jaws/services/",
        "jaws/storage/",
        "jaws/retention_cli.py",
        "jaws/evidence_cli.py",
        "jaws/settings.py",
        "jaws/optional_dependencies.py",
        "scripts/benchmark_smoke.py",
        "scripts/check_install_profiles.py",
    }
    assert all((REPO_ROOT / path).exists() for path in mypy["files"])

    policy = (REPO_ROOT / "docs" / "quality.md").read_text(encoding="utf-8")
    assert "may expand but may not shrink" in policy


def test_ci_workflow_covers_every_change_and_required_signals():
    workflow = _workflow(CI_PATH)
    assert {"push", "pull_request", "workflow_dispatch"} <= set(workflow["on"])
    assert {"quality", "correctness", "benchmark-smoke"} == set(workflow["jobs"])

    commands = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )
    assert "python -m ruff check" in commands
    assert "python -m ruff format --check" in commands
    assert "python -m mypy" in commands
    assert 'python -m pytest -m "not neo4j and not recall"' in commands
    assert "python scripts/benchmark_smoke.py" in commands
    assert "pytest -m recall" not in commands

    correctness_checkout = next(
        step
        for step in workflow["jobs"]["correctness"]["steps"]
        if step.get("uses") == "actions/checkout@v7"
    )
    assert correctness_checkout["with"]["fetch-depth"] == "0"


def test_integration_workflow_is_optional_and_resource_specific():
    workflow = _workflow(INTEGRATION_PATH)
    assert set(workflow["on"]) == {"workflow_dispatch", "schedule"}
    assert set(workflow["jobs"]) == {"neo4j", "capture-tooling"}
    assert (
        workflow["jobs"]["neo4j"]["services"]["neo4j"]["image"] == "neo4j:5.26.28-community-ubi10"
    )
    password = workflow["jobs"]["neo4j"]["env"]["NEO4J_PASSWORD"]
    assert "${{ github.run_id }}" in password
    assert "jaws-ci-password" not in INTEGRATION_PATH.read_text(encoding="utf-8")

    commands = "\n".join(
        step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]
    )
    assert "python -m pytest -m neo4j" in commands
    assert "tshark --version" in commands


def test_dependency_caches_include_the_reviewed_resolution_and_models_are_uncached():
    for path in (CI_PATH, INTEGRATION_PATH):
        workflow = _workflow(path)
        setup_steps = _steps_with_action(workflow, "actions/setup-python@v7")
        assert setup_steps
        for step in setup_steps:
            assert step["with"]["cache"] == "pip"
            dependency_paths = set(step["with"]["cache-dependency-path"].splitlines())
            assert {
                "constraints/py312-direct.txt",
                "pyproject.toml",
                "requirements-dev.txt",
            } <= dependency_paths

        text = path.read_text(encoding="utf-8").lower()
        assert "huggingface" not in text
        assert "hf_home" not in text


def test_benchmark_smoke_is_report_only_and_structurally_complete(tmp_path):
    output_dir = tmp_path / "smoke"
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "benchmark_smoke.py"),
            "--output-dir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    report = json.loads((output_dir / "metrics.json").read_text(encoding="utf-8"))
    markdown = (output_dir / "report.md").read_text(encoding="utf-8")
    assert report["schema_version"] == "1.0.0"
    assert report["policy"] == "report_only"
    assert report["cutoff"] == 3
    assert {row["scenario_id"] for row in report["scenarios"]} == EXPECTED_SCENARIOS
    assert report["summary"]["scenario_count"] == len(EXPECTED_SCENARIOS)
    assert all(row["quality_outcome"] in {"passed", "failed"} for row in report["scenarios"])
    assert all(
        row["rank"] is None or 1 <= row["rank"] <= row["total"] for row in report["scenarios"]
    )
    assert "report-only" in markdown
    assert "Synthetic benchmark smoke report" in result.stdout
