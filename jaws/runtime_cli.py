"""Container health, inventory, and no-live-capture smoke operations."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from jaws.adapters.experiment_bundles import ExperimentBundleStore

RUNTIME_SCHEMA_VERSION = "1.0.0"


def safe_fixture_bytes() -> bytes:
    """Return one payload-free Ethernet/IPv4/UDP packet in classic PCAP format."""

    ethernet = bytes.fromhex("0200000000020200000000010800")
    ipv4 = bytes.fromhex("4500001c0001400040110000c0000201c6336402")
    udp = bytes.fromhex("c001003500080000")
    packet = ethernet + ipv4 + udp
    global_header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    packet_header = struct.pack("<IIII", 1_725_000_000, 0, len(packet), len(packet))
    return global_header + packet_header + packet


def write_safe_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(safe_fixture_bytes())
    return path


def health(role: str) -> dict[str, Any]:
    checks: dict[str, bool] = {
        "jaws": importlib.util.find_spec("jaws") is not None,
        "numeric": all(importlib.util.find_spec(name) is not None for name in ("numpy", "sklearn")),
    }
    if role == "mcp":
        checks["mcp"] = importlib.util.find_spec("mcp") is not None
    if role == "sensor":
        checks["tshark"] = shutil.which("tshark") is not None
    if role == "gpu":
        checks["torch"] = importlib.util.find_spec("torch") is not None
        if checks["torch"]:
            torch = importlib.import_module("torch")
            checks["cuda"] = bool(torch.cuda.is_available())
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "role": role,
        "ok": all(checks.values()),
        "checks": checks,
    }


def inventory() -> dict[str, Any]:
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "container_digest": os.environ.get("JAWS_CONTAINER_DIGEST"),
        "model_digest": os.environ.get("JAWS_MODEL_DIGEST"),
        "packages": {
            distribution.metadata["Name"]: distribution.version
            for distribution in importlib.metadata.distributions()
            if distribution.metadata["Name"]
        },
    }


def pcap_smoke(path: Path, *, require_tshark: bool) -> dict[str, Any]:
    write_safe_fixture(path)
    command = shutil.which("tshark")
    parsed = False
    if command is not None:
        result = subprocess.run(
            (command, "-n", "-r", str(path), "-c", "1"),
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(f"tshark rejected the safe fixture: {result.stderr.strip()}")
        parsed = True
    elif require_tshark:
        raise RuntimeError("tshark is required for this container smoke profile")
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "ok": True,
        "live_capture": False,
        "fixture": str(path),
        "bytes": path.stat().st_size,
        "tshark_parsed": parsed,
    }


class _HealthHandler(BaseHTTPRequestHandler):
    role = "analyzer"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self.send_error(404)
            return
        result = health(self.role)
        content = (json.dumps(result, sort_keys=True) + "\n").encode()
        self.send_response(200 if result["ok"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_health(role: str, host: str, port: int) -> None:
    handler = type("RoleHealthHandler", (_HealthHandler,), {"role": role})
    server = ThreadingHTTPServer((host, port), handler)
    server.serve_forever()


def probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def verify_artifacts(root: Path) -> dict[str, Any]:
    store = ExperimentBundleStore(root)
    paths = tuple(sorted(root.glob("experiments/*/runs/*")))
    if not paths:
        raise RuntimeError("artifact root contains no experiment run bundles")
    invalid = {
        str(path.relative_to(root)): {
            "missing": result.missing,
            "mismatched": result.mismatched,
            "unexpected": result.unexpected,
        }
        for path in paths
        if not (result := store.verify(path)).valid
    }
    return {
        "schema_version": RUNTIME_SCHEMA_VERSION,
        "ok": not invalid,
        "verified_bundles": len(paths),
        "invalid": invalid,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaws-runtime", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    health_parser = commands.add_parser("health")
    health_parser.add_argument(
        "--role", choices=("analyzer", "mcp", "sensor", "gpu"), required=True
    )
    serve = commands.add_parser("serve-health")
    serve.add_argument("--role", choices=("analyzer", "mcp", "sensor", "gpu"), required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)
    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--host", default="127.0.0.1")
    probe_parser.add_argument("--port", type=int, required=True)
    inventory_parser = commands.add_parser("inventory")
    inventory_parser.add_argument("--output", type=Path)
    pcap = commands.add_parser("pcap-smoke")
    pcap.add_argument("--output", type=Path, default=Path("/tmp/jaws-safe-fixture.pcap"))
    pcap.add_argument("--require-tshark", action="store_true")
    verify = commands.add_parser("verify-artifacts")
    verify.add_argument("--root", type=Path, default=Path("/var/lib/jaws/artifacts"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "health":
            result = health(str(args.role))
            print(json.dumps(result, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "serve-health":
            serve_health(str(args.role), str(args.host), int(args.port))
            return 0
        if args.command == "probe":
            probe_ok = probe(str(args.host), int(args.port))
            print(json.dumps({"ok": probe_ok}, sort_keys=True))
            return 0 if probe_ok else 1
        if args.command == "inventory":
            document = json.dumps(inventory(), indent=2, sort_keys=True) + "\n"
            if args.output is not None:
                Path(args.output).write_text(document, encoding="utf-8")
            else:
                print(document, end="")
            return 0
        if args.command == "verify-artifacts":
            verification = verify_artifacts(Path(args.root))
            print(json.dumps(verification, sort_keys=True))
            return 0 if verification["ok"] else 1
        result = pcap_smoke(Path(args.output), require_tshark=bool(args.require_tshark))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"error": str(error), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
