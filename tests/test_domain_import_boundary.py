"""The domain package must stay importable without optional integrations."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_domain_imports_with_every_optional_stack_blocked():
    code = f"""
import builtins
import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
blocked = {{'ipinfo', 'matplotlib', 'mcp', 'neo4j', 'openai', 'pandas', 'plotille',
           'psutil', 'pyshark', 'sentence_transformers', 'sklearn', 'torch'}}
real_import = builtins.__import__
def guarded(name, globals=None, locals=None, fromlist=(), level=0):
    if name.split('.', 1)[0] in blocked:
        raise ModuleNotFoundError(name)
    return real_import(name, globals, locals, fromlist, level)
builtins.__import__ = guarded
import jaws.domain
loaded = {{name.split('.', 1)[0] for name in sys.modules}}
assert not loaded & blocked, sorted(loaded & blocked)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
