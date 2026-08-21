#!/usr/bin/env python3
"""Validate a retained JAWS release-candidate evidence record and checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE = REPOSITORY / "release" / "3.0.0-rc1"
PRIVATE_PATH = re.compile(r"(?:^|[\s\"'])/(?:home|Users)/[^\s\"']+")
SECRET = re.compile(
    r"(?i)(?:api[_-]?key|password|secret|token)\s*[:=]\s*[\"']?(?!redacted|null|unset|not-set)"
    r"[A-Za-z0-9_./+\-=]{12,}"
)


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checksums(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    checksum_file = root / "checksums.sha256"
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", 1)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid checksum for {relative}")
        path = root / relative
        if not path.is_file() or _sha256(path) != digest:
            raise ValueError(f"checksum mismatch for {relative}")
        values[relative] = digest
    return values


def _scan(paths: Sequence[Path]) -> None:
    for path in paths:
        if path.suffix not in {".json", ".md", ".sha256", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        if PRIVATE_PATH.search(text):
            raise ValueError(f"absolute private path in {path.name}")
        if SECRET.search(text):
            raise ValueError(f"possible secret in {path.name}")
        if any(suffix in text.lower() for suffix in ('.pcap"', '.pcapng"')):
            raise ValueError(f"capture locator in {path.name}")


def qualify(root: Path) -> dict[str, object]:
    qualification = _object(
        json.loads((root / "qualification.json").read_text(encoding="utf-8")),
        "qualification",
    )
    compatibility = _object(
        json.loads((root / "compatibility.json").read_text(encoding="utf-8")),
        "compatibility",
    )
    package = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = package["project"]["version"]
    if qualification.get("package_version") != package_version:
        raise ValueError("qualification package version differs from pyproject")
    if compatibility.get("package_version") != package_version:
        raise ValueError("compatibility package version differs from pyproject")
    gates = _object(qualification.get("gates"), "qualification gates")
    allowed = {"passed", "unavailable", "review-gated"}
    unexpected = {name: value for name, value in gates.items() if value not in allowed}
    if unexpected:
        raise ValueError(f"invalid gate states: {unexpected}")
    if any(value == "failed" for value in gates.values()):
        raise ValueError("qualification contains a failed gate")
    checksums = _checksums(root)
    governed = [path for path in root.iterdir() if path.name != "checksums.sha256"]
    _scan(governed)
    return {
        "ok": True,
        "release_candidate": qualification.get("release_candidate"),
        "package_version": package_version,
        "gate_counts": {state: tuple(gates.values()).count(state) for state in sorted(allowed)},
        "verified_files": len(checksums),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_RELEASE)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(qualify(Path(args.root)), sort_keys=True))
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
