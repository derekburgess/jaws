"""Thin compatibility adapter for the Milestone 4 comparison runtime."""

from __future__ import annotations

import sys

from jaws.adapters import finder_runtime as _runtime

# Preserve the historical import-and-monkeypatch surface used by Benchmark 0 and
# downstream callers while keeping this public module free of ranking, storage, MCP,
# and plotting behavior.  The adapter runtime delegates scoring to `jaws.services`.
if __name__ != "__main__":
    sys.modules[__name__] = _runtime
else:  # pragma: no cover - exercised through the installed `jaws-finder` script
    _runtime.main()
