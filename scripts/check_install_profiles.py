#!/usr/bin/env python3
"""Install JAWS capability profiles in disposable virtual environments.

The default selection covers the two lightweight release gates. Pass ``--all`` for
the complete matrix; the local-embeddings and all profiles intentionally install the
large local-model stack.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import venv
from pathlib import Path
from typing import NotRequired, TypedDict

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = REPO_ROOT / "constraints" / "py312-direct.txt"


class ProfileSpec(TypedDict):
    """One isolated installation and import probe."""

    extra: str | None
    imports: list[str]
    absent: NotRequired[list[str]]


PROFILES: dict[str, ProfileSpec] = {
    "core": {
        "extra": None,
        "imports": [
            "jaws.adapters",
            "jaws.jaws_compute",
            "jaws.jaws_finder",
            "jaws.ports",
            "jaws.storage",
        ],
        "absent": ["neo4j", "openai", "torch", "sentence_transformers", "mcp"],
    },
    "neo4j": {
        "extra": "neo4j",
        "imports": ["neo4j", "jaws.config", "jaws.storage", "jaws.storage.cli"],
    },
    "capture": {
        "extra": "capture",
        "imports": ["psutil", "pyshark", "jaws.jaws_capture"],
    },
    "enrichment": {
        "extra": "enrichment",
        "imports": ["ipinfo", "jaws.jaws_ipinfo"],
    },
    "openai-embeddings": {
        "extra": "openai-embeddings",
        "imports": ["openai", "jaws.jaws_compute"],
        "absent": ["torch", "sentence_transformers"],
    },
    "local-embeddings": {
        "extra": "local-embeddings",
        "imports": ["torch", "sentence_transformers", "jaws.jaws_compute"],
        "absent": ["openai"],
    },
    "plotting": {
        "extra": "plotting",
        "imports": ["matplotlib.pyplot", "plotille", "jaws.jaws_finder"],
    },
    "mcp": {"extra": "mcp", "imports": ["mcp", "jaws_mcp.server"]},
    "agent-lab": {
        "extra": "agent-lab",
        "imports": ["jaws"],
        "absent": ["openai", "torch", "sentence_transformers", "mcp"],
    },
    "dev": {
        "extra": "dev",
        "imports": [
            "pytest",
            "jsonschema",
            "mypy",
            "packaging",
            "psutil",
            "yaml",
            "jaws",
        ],
        "absent": ["neo4j", "openai", "torch", "sentence_transformers", "mcp"],
    },
    "all": {
        "extra": "all",
        "imports": [
            "neo4j",
            "psutil",
            "pyshark",
            "ipinfo",
            "openai",
            "torch",
            "sentence_transformers",
            "matplotlib.pyplot",
            "plotille",
            "mcp",
        ],
    },
}


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)


def _probe(python: Path, profile: str, env: dict[str, str]) -> None:
    specification = PROFILES[profile]
    code = """
import importlib
import importlib.util
import json
import sys

specification = json.loads(sys.argv[1])
for module_name in specification.get("imports", []):
    importlib.import_module(module_name)
unexpected = [
    module_name
    for module_name in specification.get("absent", [])
    if importlib.util.find_spec(module_name) is not None
]
if unexpected:
    raise SystemExit(f"unexpected optional modules installed: {unexpected}")
print(json.dumps({"imports": specification.get("imports", []), "absent": unexpected}))
"""
    _run([str(python), "-I", "-c", code, json.dumps(specification)], env=env)


def check_profile(profile: str, cache: Path) -> None:
    specification = PROFILES[profile]
    with tempfile.TemporaryDirectory(prefix=f"jaws-{profile}-") as directory:
        root = Path(directory)
        venv.EnvBuilder(with_pip=True, clear=True).create(root)
        python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = os.environ.copy()
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PIP_CACHE_DIR"] = str(cache)
        env["MPLCONFIGDIR"] = str(root / "matplotlib")
        requirement = str(REPO_ROOT)
        if specification["extra"]:
            requirement += f"[{specification['extra']}]"
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--constraint",
                str(CONSTRAINTS),
                requirement,
            ],
            env=env,
        )
        _run([str(python), "-m", "pip", "check"], env=env)
        _probe(python, profile, env)
    print(f"PASS {profile}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        action="append",
        choices=sorted(PROFILES),
        dest="profiles",
        help="Profile to verify; repeat for multiple profiles.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Verify every profile, including the large local-model installation.",
    )
    args = parser.parse_args()
    profiles = list(PROFILES) if args.all else (args.profiles or ["core", "openai-embeddings"])
    with tempfile.TemporaryDirectory(prefix="jaws-profile-cache-") as cache:
        for profile in profiles:
            check_profile(profile, Path(cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
