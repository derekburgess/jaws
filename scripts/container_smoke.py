#!/usr/bin/env python3
"""Build and exercise the CPU/sensor runtime without live capture."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def _run(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPOSITORY,
        env={**os.environ, **environment},
        check=True,
        text=True,
        capture_output=capture,
    )


def _revision() -> str:
    result = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def smoke(output: Path, *, include_neo4j: bool) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    revision = _revision()
    environment = {
        "JAWS_SOURCE_REVISION": revision,
        "JAWS_BUILD_DATE": datetime.now(UTC).isoformat(),
        "NEO4J_PASSWORD": os.environ.get("NEO4J_PASSWORD", "jaws-container-smoke-only"),
    }
    _run(
        ("docker", "compose", "-f", "compose.dev.yml", "config", "--quiet"), environment=environment
    )
    _run(
        (
            "docker",
            "build",
            "--file",
            "containers/Dockerfile",
            "--target",
            "analyzer",
            "--build-arg",
            f"SOURCE_REVISION={revision}",
            "--tag",
            "jaws-analyzer:smoke",
            ".",
        ),
        environment=environment,
    )
    image = _run(
        ("docker", "image", "inspect", "jaws-analyzer:smoke", "--format", "{{.Id}}"),
        environment=environment,
        capture=True,
    ).stdout.strip()
    _run(
        (
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--tmpfs",
            "/tmp:rw,size=128m",
            "jaws-analyzer:smoke",
            "health",
            "--role",
            "analyzer",
        ),
        environment=environment,
    )
    inventory = _run(
        (
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--env",
            f"JAWS_CONTAINER_DIGEST={image.removeprefix('sha256:')}",
            "jaws-analyzer:smoke",
            "inventory",
        ),
        environment=environment,
        capture=True,
    )
    (output / "analyzer-inventory.json").write_text(inventory.stdout, encoding="utf-8")

    _run(
        (
            "docker",
            "build",
            "--file",
            "containers/Dockerfile",
            "--target",
            "sensor",
            "--build-arg",
            f"SOURCE_REVISION={revision}",
            "--tag",
            "jaws-sensor:smoke",
            ".",
        ),
        environment=environment,
    )
    pcap = _run(
        (
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop=ALL",
            "--tmpfs",
            "/tmp:rw,size=32m",
            "jaws-sensor:smoke",
            "pcap-smoke",
            "--require-tshark",
        ),
        environment=environment,
        capture=True,
    )
    (output / "pcap-smoke.json").write_text(pcap.stdout, encoding="utf-8")

    database_checked = False
    if include_neo4j:
        try:
            _run(
                (
                    "docker",
                    "compose",
                    "-f",
                    "compose.dev.yml",
                    "up",
                    "--detach",
                    "--wait",
                    "neo4j",
                ),
                environment=environment,
            )
            _run(
                ("docker", "compose", "-f", "compose.dev.yml", "run", "--rm", "migrate"),
                environment=environment,
            )
            _run(
                ("docker", "compose", "-f", "compose.dev.yml", "restart", "neo4j"),
                environment=environment,
            )
            _run(
                ("docker", "compose", "-f", "compose.dev.yml", "run", "--rm", "migrate"),
                environment=environment,
            )
            database_checked = True
        finally:
            _run(
                ("docker", "compose", "-f", "compose.dev.yml", "down"),
                environment=environment,
            )
    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "source_revision": revision,
        "analyzer_image_digest": image,
        "cpu_health": "passed",
        "pcap_import": "passed",
        "live_capture": False,
        "database_restart_and_migration": database_checked,
    }
    (output / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("runtime-smoke"))
    parser.add_argument("--include-neo4j", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                smoke(Path(args.output_dir), include_neo4j=bool(args.include_neo4j)),
                sort_keys=True,
            )
        )
        return 0
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
