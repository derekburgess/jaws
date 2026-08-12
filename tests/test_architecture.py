"""Static import-direction contracts for the deterministic inner packages."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

LAYERS = {
    "jaws.domain": {"jaws.domain"},
    "jaws.ports": {"jaws.domain", "jaws.ports"},
    "jaws.settings": {"jaws.domain", "jaws.settings"},
}


def _governed_files(package: str) -> list[Path]:
    path = REPO_ROOT.joinpath(*package.split("."))
    if path.is_file():
        return [path]
    if path.with_suffix(".py").is_file():
        return [path.with_suffix(".py")]
    return sorted(path.rglob("*.py"))


def _absolute_imports(path: Path, package: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imports.add(node.module)
            elif node.level > 0:
                relative = package.split(".")
                if node.level > 1:
                    relative = relative[: -(node.level - 1)]
                if node.module:
                    relative.extend(node.module.split("."))
                imports.add(".".join(relative))
    return imports


def _owner(module: str) -> str | None:
    matches = [
        package for package in LAYERS if module == package or module.startswith(package + ".")
    ]
    return max(matches, key=len) if matches else None


def test_inner_packages_follow_documented_import_directions():
    violations = []
    for package, allowed in LAYERS.items():
        files = _governed_files(package)
        assert files, f"governed package is missing: {package}"
        for path in files:
            for imported in _absolute_imports(path, package):
                root = imported.split(".", 1)[0]
                if root != "jaws":
                    if root not in sys.stdlib_module_names:
                        violations.append(
                            f"{path.relative_to(REPO_ROOT)} imports third-party module {imported}"
                        )
                    continue
                owner = _owner(imported)
                if owner not in allowed:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)} imports {imported} ({owner or 'ungoverned'})"
                    )
    assert not violations, "invalid inward dependency:\n" + "\n".join(violations)


def test_architecture_policy_names_every_enforced_layer():
    policy = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    assert set(LAYERS) <= {package for package in LAYERS if f"`{package}`" in policy}


def test_capture_cli_delegates_cypher_to_storage_adapters():
    source = (REPO_ROOT / "jaws" / "jaws_capture.py").read_text(encoding="utf-8")
    assert not any(
        token in source for token in ("MATCH (", "MERGE (", "CREATE (", "UNWIND $", "session.run(")
    )


def test_mcp_adapter_delegates_cypher_to_storage_adapters():
    source = (REPO_ROOT / "jaws_mcp" / "server.py").read_text(encoding="utf-8")
    assert not any(
        token in source for token in ("MATCH (", "MERGE (", "CREATE (", "UNWIND $", "session.run(")
    )
