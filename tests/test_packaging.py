"""Dependency metadata, constraints, and lightweight import-boundary checks."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from jaws.optional_dependencies import require_module

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PROJECT = PYPROJECT["project"]
EXTRAS = PROJECT["optional-dependencies"]
CORE_NAMES = {"kneed", "numpy", "pandas", "rich", "scikit-learn"}
CAPABILITY_EXTRAS = {
    "neo4j",
    "capture",
    "enrichment",
    "openai-embeddings",
    "local-embeddings",
    "plotting",
    "mcp",
    "agent-lab",
}


def _names(requirements: list[str]) -> set[str]:
    return {Requirement(value).name.lower() for value in requirements}


def _constraints() -> dict[str, Requirement]:
    rows = {}
    path = REPO_ROOT / "constraints" / "py312-direct.txt"
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        requirement = Requirement(value)
        rows[requirement.name.lower()] = requirement
    return rows


def test_capability_groups_are_explicit_and_complete():
    assert _names(PROJECT["dependencies"]) == CORE_NAMES
    assert CAPABILITY_EXTRAS <= set(EXTRAS)
    assert {"all", "dev"} <= set(EXTRAS)
    assert EXTRAS["agent-lab"] == []

    runtime_union = set().union(*(_names(EXTRAS[name]) for name in CAPABILITY_EXTRAS))
    assert _names(EXTRAS["all"]) == runtime_union


def test_lightweight_and_openai_profiles_exclude_local_model_dependencies():
    heavy = {"torch", "sentence-transformers"}
    assert not (CORE_NAMES & heavy)
    assert not (_names(EXTRAS["dev"]) & heavy)
    assert not (_names(EXTRAS["openai-embeddings"]) & heavy)
    assert _names(EXTRAS["local-embeddings"]) == heavy


def test_exact_constraints_cover_declared_direct_dependencies():
    constraints = _constraints()
    declared = list(PROJECT["dependencies"])
    for values in EXTRAS.values():
        declared.extend(values)
    declared.extend(PYPROJECT["build-system"]["requires"])

    for value in declared:
        requirement = Requirement(value)
        constrained = constraints[requirement.name.lower()]
        specs = list(constrained.specifier)
        assert len(specs) == 1 and specs[0].operator == "==", constrained
        version = Version(specs[0].version)
        assert version in requirement.specifier, (requirement, constrained)


def test_requirements_entry_points_apply_reviewed_constraints():
    assert (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()[-2:] == [
        "-c constraints/py312-direct.txt",
        ".[all]",
    ]
    assert (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()[-2:] == [
        "-c constraints/py312-direct.txt",
        "-e .[dev]",
    ]


def test_schema_administration_entry_point_is_packaged():
    assert PROJECT["scripts"]["jaws-schema"] == "jaws.storage.cli:main"


def test_retention_entry_point_is_packaged():
    assert PROJECT["scripts"]["jaws-retention"] == "jaws.retention_cli:main"


def test_evidence_entry_point_is_packaged():
    assert PROJECT["scripts"]["jaws-evidence"] == "jaws.evidence_cli:main"
    assert PROJECT["scripts"]["jaws-admin"] == "jaws.administration_cli:main"


def test_missing_capability_error_names_the_install_extra():
    try:
        require_module(
            "jaws_intentionally_missing_dependency",
            "agent-lab",
            "Fixture capability",
        )
    except ModuleNotFoundError as error:
        assert "Fixture capability" in str(error)
        assert "JAWS[agent-lab]" in str(error)
    else:
        raise AssertionError("fixture-only module unexpectedly imported")


def test_mcp_adapter_uses_the_declared_v2_server_api():
    assert Version("2.0.0") in Requirement(EXTRAS["mcp"][0]).specifier
    source = (REPO_ROOT / "jaws_mcp" / "server.py").read_text(encoding="utf-8")
    assert "from mcp.server import MCPServer" in source
    assert "mcp.server.fastmcp" not in source


def test_numeric_benchmark_runs_with_optional_integrations_blocked():
    code = f"""
import builtins
import os
import sys

sys.path.insert(0, {str(REPO_ROOT)!r})
sys.path.insert(0, {str(REPO_ROOT / "tests")!r})
blocked = {{
    'ipinfo', 'matplotlib', 'mcp', 'neo4j', 'openai', 'plotille', 'psutil',
    'pyshark', 'sentence_transformers', 'torch'
}}
real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.', 1)[0] in blocked:
        raise ModuleNotFoundError(f'blocked optional import: {{name}}')
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
os.environ['JAWS_RECALL_SOURCE'] = 'synthetic'
from harness import all_scenarios, evaluate

results = [evaluate(scenario) for scenario in all_scenarios('synthetic')]
assert len(results) == 8
assert sum(row['passed'] for row in results if row['scenario'].expect == 'detect') == 5
loaded = {{name.split('.', 1)[0] for name in sys.modules}}
assert not (loaded & blocked), sorted(loaded & blocked)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
