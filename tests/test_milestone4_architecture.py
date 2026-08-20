"""Completion-gate boundaries for comparison and finder components."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
CORE = (
    "comparison.py",
    "references.py",
    "legacy_ranking.py",
    "explanations.py",
    "inspection.py",
)
FORBIDDEN_ROOTS = {"argparse", "matplotlib", "neo4j", "plotille", "jaws_mcp"}


def test_comparison_services_do_not_import_storage_interfaces_or_plotting():
    violations = []
    for name in CORE:
        path = ROOT / "jaws/services" / name
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                root = module.split(".", 1)[0]
                if root in FORBIDDEN_ROOTS or module.startswith("jaws.storage"):
                    violations.append(f"{name}: {module}")
    assert violations == []


def test_public_finder_module_is_only_a_compatibility_adapter():
    source = (ROOT / "jaws/jaws_finder.py").read_text()
    assert len(source.splitlines()) < 20
    for forbidden in ("DBSCAN", "Neo4j", "matplotlib", "MATCH (", "def score_"):
        assert forbidden not in source
