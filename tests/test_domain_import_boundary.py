"""Domain, ports, and settings must import without optional integrations."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# `rich` is intentionally absent: it is a core dependency, and jaws.config builds a
# Console at import. Everything here is an optional capability extra (see ADR-0008).
BLOCKED = (
    "ipinfo, matplotlib, mcp, neo4j, openai, pandas, plotille, "
    "psutil, pyshark, sentence_transformers, sklearn, torch"
)


def _run_with_optional_stacks_blocked(body: str) -> subprocess.CompletedProcess[str]:
    code = f"""
import builtins
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
blocked = set({BLOCKED!r}.replace(' ', '').split(','))
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.', 1)[0] in blocked:
        raise ModuleNotFoundError(name)
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
{body}
loaded = {{name.split('.', 1)[0] for name in sys.modules}}
assert not loaded & blocked, sorted(loaded & blocked)
"""
    return subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "module",
    ["jaws.domain", "jaws.ports", "jaws.settings", "jaws.storage", "jaws.adapters.runtime"],
)
def test_module_imports_with_every_optional_stack_blocked(module):
    result = _run_with_optional_stacks_blocked(f"import {module}")
    assert result.returncode == 0, result.stderr


def test_settings_load_without_any_optional_dependency_or_credential():
    """Building settings must not reach for a driver, a provider, or a secret."""
    result = _run_with_optional_stacks_blocked(
        "from jaws.settings import load_settings\nload_settings({})"
    )
    assert result.returncode == 0, result.stderr
