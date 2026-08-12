"""Collect and validate the remaining Benchmark 0 compatibility inventories.

The ranking collector froze detector quality.  This module freezes the two adapter and
storage contracts that must also survive the refactor:

* agent-mode CLI stdout/stderr/exit behavior under deterministic fakes; and
* the Neo4j labels, properties, relationships, indexes, constraints, joins, and
  lifecycle implied by every Cypher-bearing source location.

Canonical collection is intentionally a second-stage operation.  The collector must
first exist at a clean, committed revision; the generated inventories then name and
verify that revision independently from the original ranking collector.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable
from unittest.mock import patch

import numpy as np
import pandas as pd

from jaws.jaws_utils import Reporter

from .benchmark_contract import (
    DEFAULT_BASELINE,
    DEFAULT_SUBJECT_REVISION,
    REPO_ROOT,
    SECRET_PATTERNS,
    ContractViolation,
    _git_file,
    _resolve_commit,
    _tree_digest,
    _tree_digest_at_revision,
    _worktree_changes,
    sha256_bytes,
    validate_bundle,
    write_checksums,
)

COMPATIBILITY_SCHEMA_VERSION = "1.0.0"
COMPATIBILITY_DIR = DEFAULT_BASELINE / "compatibility"
COLLECTOR_SOURCE_PATHS = ("tests/harness/compatibility_contract.py",)
GRAPH_SOURCE_PATHS = (
    "jaws/jaws_capture.py",
    "jaws/jaws_compute.py",
    "jaws/jaws_finder.py",
    "jaws/jaws_ipinfo.py",
    "jaws/jaws_utils.py",
    "jaws_mcp/server.py",
)
CLI_SUBJECT_PATHS = (
    "jaws/config.py",
    "jaws/jaws_utils.py",
    "jaws/jaws_capture.py",
    "jaws/jaws_compute.py",
    "jaws/jaws_finder.py",
)
COMPATIBILITY_ARTIFACTS = (
    ("compatibility/cli-contract.json", "application/json"),
    ("compatibility/neo4j-schema.json", "application/json"),
    ("compatibility/README.md", "text/markdown"),
)


def _json_document(value: Any) -> str:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def _write_json(path: Path, value: Any) -> None:
    path.write_text(_json_document(value), encoding="utf-8")


def _source_record(revision: str, path: str) -> dict[str, str]:
    content = _git_file(revision, path)
    return {"path": path, "sha256": sha256_bytes(content)}


def _source_records(revision: str, paths: Iterable[str]) -> list[dict[str, str]]:
    return [_source_record(revision, path) for path in paths]


def _validate_source_records(records: Any, revision: str, paths: Iterable[str], name: str) -> None:
    expected = _source_records(revision, paths)
    if records != expected:
        raise ContractViolation(f"{name} source-file inventory drifted")


def _verify_subject_sources(revision: str, paths: Iterable[str]) -> None:
    for path in paths:
        subject = _git_file(revision, path)
        current = (REPO_ROOT / path).read_bytes()
        if current != subject:
            raise ContractViolation(
                f"{path} differs from subject revision {revision}; compatibility "
                "collection would describe changed behavior"
            )


def _collector_record(revision: str) -> dict[str, Any]:
    if revision == "worktree":
        return {
            "revision": revision,
            "working_tree_dirty": True,
            "source_sha256": _tree_digest(list(COLLECTOR_SOURCE_PATHS)),
            "source_paths": list(COLLECTOR_SOURCE_PATHS),
        }
    return {
        "revision": revision,
        "working_tree_dirty": False,
        "source_sha256": _tree_digest_at_revision(revision, list(COLLECTOR_SOURCE_PATHS)),
        "source_paths": list(COLLECTOR_SOURCE_PATHS),
    }


class _FakeDriver:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _agent_reporter() -> Reporter:
    return Reporter(agent=True)


def _normalize_exit_code(code: Any) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def _invoke_main(
    *,
    case_id: str,
    entry_point: str,
    argv: list[str],
    category: str,
    description: str,
    main: Callable[[], Any],
    patchers: Iterable[Any] = (),
) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = 0
    with ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        stack.enter_context(patch.object(sys, "argv", argv))
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                result = main()
                if isinstance(result, int):
                    exit_code = result
            except SystemExit as exc:
                exit_code = _normalize_exit_code(exc.code)

    stdout_text = stdout.getvalue()
    stderr_text = stderr.getvalue()
    parsed_stdout = None
    output_kind = "no_stdout"
    if stdout_text:
        try:
            parsed_stdout = json.loads(stdout_text)
            output_kind = "json_envelope"
        except json.JSONDecodeError:
            output_kind = "text"
    return {
        "case_id": case_id,
        "entry_point": entry_point,
        "argv": argv,
        "category": category,
        "description": description,
        "fixture_mode": "in-process main() with deterministic patched boundaries",
        "exit_code": exit_code,
        "stdout_kind": output_kind,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "parsed_stdout": parsed_stdout,
        "stdout_sha256": sha256_bytes(stdout_text.encode("utf-8")),
        "stderr_sha256": sha256_bytes(stderr_text.encode("utf-8")),
    }


def _capture_list_case() -> dict[str, Any]:
    from jaws import jaws_capture as capture

    return _invoke_main(
        case_id="capture-list-success",
        entry_point="jaws-capture",
        argv=["jaws-capture", "--list"],
        category="success",
        description="List active capture interfaces without touching Neo4j.",
        main=capture.main,
        patchers=(
            patch.object(capture, "Reporter", _agent_reporter),
            patch.object(capture, "list_interfaces", return_value=["en0", "eth0"]),
        ),
    )


def _profile(ip: str, scale: int) -> dict[str, Any]:
    return {
        "ip_address": ip,
        "endpoint_type": "public",
        "org": f"AS{scale} Example",
        "hostname": f"host-{scale}.example",
        "location": "Test City, TS, US",
        "bytes_out": 1000 * scale,
        "packets_out": 10 * scale,
        "out_peers": scale,
        "out_ports": [443],
        "bytes_in": 500 * scale,
        "packets_in": 5 * scale,
        "in_peers": scale,
        "in_ports": [55000 + scale],
        "protocols": ["TCP"],
        "interval_mean": 5.0,
        "interval_cv": 0.2,
    }


def _compute_success_case() -> dict[str, Any]:
    from jaws import jaws_compute as compute

    driver = _FakeDriver()
    packets = pd.DataFrame(
        [
            {"src_ip": "10.0.0.2", "dst_ip": "8.8.8.8", "size": 100},
            {"src_ip": "10.0.0.2", "dst_ip": "1.1.1.1", "size": 120},
            {"src_ip": "8.8.8.8", "dst_ip": "10.0.0.2", "size": 80},
        ]
    )
    profiles = [_profile("8.8.8.8", 1), _profile("1.1.1.1", 2)]
    return _invoke_main(
        case_id="compute-success",
        entry_point="jaws-compute",
        argv=[
            "jaws-compute",
            "--api",
            "openai",
            "--database",
            "fixtures",
            "--session",
            "latest",
        ],
        category="success",
        description="Profile one capture session and emit the complete success envelope.",
        main=compute.main,
        patchers=(
            patch.object(compute, "Reporter", _agent_reporter),
            patch.object(compute, "dbms_connection", return_value=driver),
            patch.object(
                compute.Neo4jRepositories,
                "connect",
                return_value=SimpleNamespace(profiles=object()),
            ),
            patch.object(
                compute,
                "resolve_session",
                return_value=("20260102T000000Z", ["20260101T000000Z", "20260102T000000Z"]),
            ),
            patch.object(compute, "fetch_packets", return_value=packets),
            patch.object(compute, "fetch_ip_metadata", return_value={}),
            patch.object(compute, "build_endpoint_profiles", return_value=profiles),
            patch.object(compute, "get_openai_client", return_value=object()),
            patch.object(
                compute,
                "compute_openai_embeddings",
                return_value=[[0.1, 0.2], [0.3, 0.4]],
            ),
            patch.object(compute, "replace_session_profiles", return_value=2),
            patch.object(compute, "prune_profile_sessions", return_value=(0, [])),
            patch.object(compute, "count_profile_sessions", return_value=2),
        ),
    )


@dataclass
class _FakePCA:
    explained_variance_ratio_: np.ndarray


class _FakeNearestNeighbors:
    def __init__(self, n_neighbors: int) -> None:
        self.n_neighbors = n_neighbors

    def fit(self, features: np.ndarray) -> "_FakeNearestNeighbors":
        return self

    def kneighbors(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        count = len(features)
        distances = np.tile(np.linspace(0.0, 0.4, self.n_neighbors), (count, 1))
        indices = np.tile(np.arange(self.n_neighbors), (count, 1))
        return distances, indices


class _FakeDBSCAN:
    def __init__(self, eps: float, min_samples: int) -> None:
        self.eps = eps
        self.min_samples = min_samples

    def fit_predict(self, features: np.ndarray) -> np.ndarray:
        return np.array([0, 0, 0, -1], dtype=int)


class _FakePlotilleFigure:
    color_mode = "byte"
    width = 80
    height = 20

    def scatter(self, *args: Any, **kwargs: Any) -> None:
        return None

    def show(self, legend: bool = False) -> str:
        return "fixture plot"


def _rank_data() -> list[dict[str, Any]]:
    rows = []
    for index, ip in enumerate(("8.8.8.8", "1.1.1.1", "9.9.9.9", "4.2.2.2"), start=1):
        rows.append(
            {
                "ip_address": ip,
                "endpoint_type": "public",
                "capture_id": "20260102T000000Z",
                "org": f"AS{index} Example",
                "hostname": f"peer-{index}.example",
                "location": "Test City, TS, US",
                "bytes_out": index * 1000,
                "packets_out": index * 10,
                "out_peers": index,
                "bytes_in": index * 500,
                "packets_in": index * 5,
                "in_peers": index,
                "interval_mean": float(index),
                "interval_cv": 0.1 * index,
            }
        )
    return rows


def _ranked_endpoints() -> list[dict[str, Any]]:
    return [
        {
            "ip_address": "4.2.2.2",
            "anomaly_score": 8.5,
            "is_outlier": True,
            "baseline_sessions": 0,
            "first_seen": True,
            "reasons": [
                {
                    "feature": "bytes_in",
                    "value": 2000.0,
                    "unit": "bytes",
                    "robust_z": 4.0,
                    "direction": "high",
                    "compared_to": "peer endpoints",
                    "baseline": 1000.0,
                    "host_relative": "traffic sent by the host to this remote endpoint",
                }
            ],
        },
        {
            "ip_address": "9.9.9.9",
            "anomaly_score": 4.0,
            "is_outlier": False,
            "baseline_sessions": 0,
            "first_seen": True,
            "reasons": [],
        },
        {
            "ip_address": "1.1.1.1",
            "anomaly_score": 2.0,
            "is_outlier": False,
            "baseline_sessions": 0,
            "first_seen": True,
            "reasons": [],
        },
        {
            "ip_address": "8.8.8.8",
            "anomaly_score": 1.0,
            "is_outlier": False,
            "baseline_sessions": 0,
            "first_seen": True,
            "reasons": [],
        },
    ]


def _host_rows() -> list[dict[str, Any]]:
    return [
        {
            "ip_address": "8.8.8.8",
            "org": "AS15169 Google LLC",
            "hostname": "dns.google",
            "location": "United States",
            "upload_bytes": 5000,
            "upload_packets": 25,
            "download_bytes": 100,
            "download_packets": 2,
        },
        {
            "ip_address": "1.1.1.1",
            "org": "AS13335 Cloudflare, Inc.",
            "hostname": "one.one.one.one",
            "location": "United States",
            "upload_bytes": 500,
            "upload_packets": 5,
            "download_bytes": 1000,
            "download_packets": 10,
        },
    ]


def _host_ranked() -> list[dict[str, Any]]:
    return [
        {
            **_host_rows()[0],
            "upload_download_ratio": 49.505,
            "bytes_per_packet": 196.0784,
            "outbound_score": 7.25,
            "is_flagged": True,
            "reasons": [
                {
                    "feature": "upload_download_ratio",
                    "value": 49.505,
                    "unit": "ratio",
                    "robust_z": 6.0,
                    "direction": "high",
                    "compared_to": "host outbound destinations",
                    "baseline": 1.0,
                    "host_relative": "host upload divided by download from this destination",
                }
            ],
        },
        {
            **_host_rows()[1],
            "upload_download_ratio": 0.4995,
            "bytes_per_packet": 99.9334,
            "outbound_score": 1.0,
            "is_flagged": False,
            "reasons": [],
        },
    ]


def _rank_success_case() -> dict[str, Any]:
    from jaws import jaws_finder as finder

    driver = _FakeDriver()
    data = _rank_data()
    embeddings = [np.array([float(i), float(i + 1)]) for i in range(4)]
    features = np.array([[-1.0, -1.0], [-0.3, -0.2], [0.3, 0.2], [1.0, 1.0]], dtype=float)

    def no_op(*args, **kwargs):
        return None

    fake_plt = SimpleNamespace(
        **{
            name: no_op
            for name in (
                "figure",
                "scatter",
                "annotate",
                "grid",
                "xticks",
                "yticks",
                "tight_layout",
                "savefig",
                "show",
            )
        }
    )
    patchers = [
        patch.object(finder, "Reporter", _agent_reporter),
        patch.object(finder, "dbms_connection", return_value=driver),
        patch.object(
            finder.Neo4jRepositories,
            "connect",
            return_value=SimpleNamespace(profiles=object()),
        ),
        patch.object(
            finder,
            "resolve_profile_scope",
            return_value=(
                "20260102T000000Z",
                ["20260102T000000Z", "20260101T000000Z"],
            ),
        ),
        patch.object(
            finder,
            "fetch_data_for_dbscan",
            return_value=(embeddings, data, 1, []),
        ),
        patch.object(finder, "fetch_data_for_portsize", return_value=[]),
        patch.object(
            finder,
            "build_feature_matrix",
            return_value=(features, _FakePCA(np.array([0.7, 0.2]))),
        ),
        patch.object(finder, "NearestNeighbors", _FakeNearestNeighbors),
        patch.object(finder, "DBSCAN", _FakeDBSCAN),
        patch.object(finder, "plt", fake_plt),
        patch.object(finder, "_load_plotting", return_value=(fake_plt, None)),
        patch.object(finder, "new_plotille_figure", return_value=_FakePlotilleFigure()),
        patch.object(finder, "fetch_endpoint_history", return_value={}),
        patch.object(finder, "score_endpoints", return_value=_ranked_endpoints()),
        patch.object(finder, "add_outlier_to_database", return_value=None),
        patch.object(
            finder,
            "fetch_host_outbound",
            return_value=(["10.0.0.2"], _host_rows()),
        ),
        patch.object(finder, "score_host_outbound", return_value=_host_ranked()),
    ]
    return _invoke_main(
        case_id="rank-success",
        entry_point="jaws-finder",
        argv=[
            "jaws-finder",
            "--database",
            "fixtures",
            "--session",
            "latest",
            "--eps",
            "0.5",
        ],
        category="success",
        description=(
            "Rank endpoint and host-outbound surfaces and emit the complete success envelope."
        ),
        main=finder.main,
        patchers=patchers,
    )


def _compute_unknown_session_case() -> dict[str, Any]:
    from jaws import jaws_compute as compute

    driver = _FakeDriver()
    return _invoke_main(
        case_id="compute-unknown-session",
        entry_point="jaws-compute",
        argv=[
            "jaws-compute",
            "--database",
            "fixtures",
            "--session",
            "missing",
        ],
        category="handled_error",
        description="Reject a requested capture session that does not exist.",
        main=compute.main,
        patchers=(
            patch.object(compute, "Reporter", _agent_reporter),
            patch.object(compute, "dbms_connection", return_value=driver),
            patch.object(
                compute.Neo4jRepositories,
                "connect",
                return_value=SimpleNamespace(profiles=object()),
            ),
            patch.object(
                compute,
                "resolve_session",
                side_effect=ValueError(
                    "session 'missing' not found; available: ['20260102T000000Z']"
                ),
            ),
        ),
    )


def _capture_missing_file_case() -> dict[str, Any]:
    from jaws import jaws_capture as capture

    driver = _FakeDriver()
    missing = "/fixtures/missing.pcap"
    return _invoke_main(
        case_id="capture-missing-file",
        entry_point="jaws-capture",
        argv=["jaws-capture", "--file", missing, "--database", "fixtures"],
        category="handled_error",
        description="Reject a PCAP path that is not present after connecting.",
        main=capture.main,
        patchers=(
            patch.object(capture, "Reporter", _agent_reporter),
            patch.object(capture, "get_local_ip", return_value="10.0.0.2"),
            patch.object(capture, "dbms_connection", return_value=driver),
            patch.object(capture, "initialize_schema", return_value=None),
            patch.object(capture.os.path, "isfile", return_value=False),
        ),
    )


def _neo4j_unavailable_case() -> dict[str, Any]:
    from jaws import jaws_compute as compute

    def unavailable(database: str, reporter: Reporter) -> None:
        reporter.error(
            "ERROR",
            "Could not connect to Neo4j (fixture: NEO4J_PASSWORD is not set).",
        )
        return None

    return _invoke_main(
        case_id="compute-neo4j-unavailable",
        entry_point="jaws-compute",
        argv=["jaws-compute", "--database", "fixtures"],
        category="handled_error",
        description="Surface a database configuration/runtime failure as JSON.",
        main=compute.main,
        patchers=(
            patch.object(compute, "Reporter", _agent_reporter),
            patch.object(compute, "dbms_connection", side_effect=unavailable),
        ),
    )


def _argparse_failure_case() -> dict[str, Any]:
    from jaws import jaws_capture as capture

    return _invoke_main(
        case_id="capture-invalid-duration",
        entry_point="jaws-capture",
        argv=["jaws-capture", "--duration", "not-an-integer"],
        category="argument_error",
        description="Let argparse reject an invalid integer before Reporter exists.",
        main=capture.main,
        patchers=(patch.object(capture, "Reporter", _agent_reporter),),
    )


def collect_cli_contract(
    subject_revision: str,
    collector_revision: str,
) -> dict[str, Any]:
    cases = [
        _capture_list_case(),
        _compute_success_case(),
        _rank_success_case(),
        _compute_unknown_session_case(),
        _capture_missing_file_case(),
        _neo4j_unavailable_case(),
        _argparse_failure_case(),
    ]
    document = {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "artifact_kind": "cli_contract",
        "benchmark_id": "baseline-0",
        "subject": {
            "revision": subject_revision,
            "source_files": _source_records(subject_revision, CLI_SUBJECT_PATHS),
        },
        "collector": _collector_record(collector_revision),
        "surface": {
            "mode": "agent",
            "selection": ("Reporter(agent=True), matching non-TTY subprocess capture used by MCP"),
            "invocation": (
                "Each entry-point main() is called with deterministic patched network, "
                "database, model, plotting, and packet boundaries; stdout, stderr, and "
                "SystemExit are captured exactly."
            ),
            "scope": [
                "capture interface listing",
                "profile computation",
                "endpoint ranking",
                "host-outbound ranking",
                "missing session",
                "missing PCAP",
                "Neo4j unavailable",
                "argument validation",
            ],
        },
        "conventions": {
            "success_envelope": {"required": ["ok"], "ok": True},
            "handled_error_envelope": {"required": ["ok", "error"], "ok": False},
            "stdout_json_documents": "exactly one for Reporter result/error paths",
            "progress_stream": "stderr in agent mode",
            "exit_status": (
                "Recorded observationally; Benchmark 0 does not normalize or repair it."
            ),
        },
        "cases": cases,
        "observations": [
            {
                "observation_id": "CLI-B0-001",
                "summary": "Reporter success paths emit one JSON object with ok=true.",
            },
            {
                "observation_id": "CLI-B0-002",
                "summary": (
                    "Handled application/configuration failures emit ok=false JSON but "
                    "currently return process exit status 0."
                ),
            },
            {
                "observation_id": "CLI-B0-003",
                "summary": (
                    "argparse validation bypasses Reporter: it emits usage/error text on "
                    "stderr, no JSON on stdout, and exits 2."
                ),
            },
            {
                "observation_id": "CLI-B0-004",
                "summary": (
                    "Agent-mode progress narration is stderr-only; the final JSON stdout "
                    "remains parseable as one document."
                ),
            },
        ],
    }
    validate_cli_contract(document)
    return document


EXPECTED_CLI_CASES = {
    "capture-list-success",
    "compute-success",
    "rank-success",
    "compute-unknown-session",
    "capture-missing-file",
    "compute-neo4j-unavailable",
    "capture-invalid-duration",
}


def _scan_text_for_secrets(text: str, name: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise ContractViolation(f"potential secret material in {name}")


def validate_cli_contract(document: dict[str, Any]) -> None:
    if document.get("schema_version") != COMPATIBILITY_SCHEMA_VERSION:
        raise ContractViolation("unsupported CLI compatibility schema version")
    if document.get("artifact_kind") != "cli_contract":
        raise ContractViolation("CLI compatibility artifact kind is invalid")
    subject = document.get("subject", {})
    revision = subject.get("revision")
    if not revision:
        raise ContractViolation("CLI compatibility artifact lacks a subject revision")
    _validate_source_records(subject.get("source_files"), revision, CLI_SUBJECT_PATHS, "CLI")
    cases = document.get("cases")
    if not isinstance(cases, list):
        raise ContractViolation("CLI compatibility cases must be a list")
    case_ids = [row.get("case_id") for row in cases]
    if len(case_ids) != len(set(case_ids)) or set(case_ids) != EXPECTED_CLI_CASES:
        raise ContractViolation("CLI compatibility case inventory is incomplete")
    for row in cases:
        for key in (
            "entry_point",
            "argv",
            "category",
            "exit_code",
            "stdout_kind",
            "stdout",
            "stderr",
            "parsed_stdout",
            "stdout_sha256",
            "stderr_sha256",
        ):
            if key not in row:
                raise ContractViolation(f"{row['case_id']}: missing {key}")
        if row["stdout_sha256"] != sha256_bytes(row["stdout"].encode("utf-8")):
            raise ContractViolation(f"{row['case_id']}: stdout digest mismatch")
        if row["stderr_sha256"] != sha256_bytes(row["stderr"].encode("utf-8")):
            raise ContractViolation(f"{row['case_id']}: stderr digest mismatch")
        _scan_text_for_secrets(row["stdout"], f"{row['case_id']} stdout")
        _scan_text_for_secrets(row["stderr"], f"{row['case_id']} stderr")
        if row["stdout_kind"] == "json_envelope":
            parsed = json.loads(row["stdout"])
            if parsed != row["parsed_stdout"]:
                raise ContractViolation(f"{row['case_id']}: parsed stdout drift")
            if row["category"] == "success" and parsed.get("ok") is not True:
                raise ContractViolation(f"{row['case_id']}: success envelope is not ok")
            if row["category"] == "handled_error":
                if parsed.get("ok") is not False or not isinstance(parsed.get("error"), str):
                    raise ContractViolation(f"{row['case_id']}: handled error envelope is invalid")
                if row["exit_code"] != 0:
                    raise ContractViolation(
                        f"{row['case_id']}: Benchmark 0 handled-error exit changed"
                    )
        elif row["category"] != "argument_error":
            raise ContractViolation(f"{row['case_id']}: expected a JSON envelope")
    argument = next(row for row in cases if row["category"] == "argument_error")
    if (
        argument["exit_code"] != 2
        or argument["stdout"]
        or argument["parsed_stdout"] is not None
        or "error: argument --duration" not in argument["stderr"]
    ):
        raise ContractViolation("argparse compatibility behavior drifted")
    rank = next(row for row in cases if row["case_id"] == "rank-success")
    payload = rank["parsed_stdout"]
    if not payload.get("endpoints") or not payload.get("host_outbound", {}).get("destinations"):
        raise ContractViolation("rank contract lacks an endpoint or host-outbound surface")


QUERY_START = re.compile(
    r"\b(?:MATCH|MERGE|CREATE|CALL)\s*\(|CREATE\s+(?:INDEX|CONSTRAINT)\b|DETACH\s+DELETE",
    re.IGNORECASE,
)
NODE_PATTERN = re.compile(r"\(\s*([A-Za-z_]\w*)?\s*:\s*([A-Z][A-Z0-9_]*)\s*(?:\{([^}]*)\})?")
RELATIONSHIP_PATTERN = re.compile(r"\[\s*(?:[A-Za-z_]\w*)?\s*:\s*([A-Z][A-Z0-9_]*)")


@dataclass(frozen=True)
class _CypherString:
    path: str
    function: str
    line: int
    text: str


class _CypherVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.functions: list[str] = []
        self.queries: list[_CypherString] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and QUERY_START.search(node.value):
            self.queries.append(
                _CypherString(
                    path=self.path,
                    function=self.functions[-1] if self.functions else "<module>",
                    line=node.lineno,
                    text=node.value,
                )
            )


def _cypher_strings(subject_revision: str) -> list[_CypherString]:
    queries: list[_CypherString] = []
    for path in GRAPH_SOURCE_PATHS:
        source = _git_file(subject_revision, path).decode("utf-8")
        visitor = _CypherVisitor(path)
        visitor.visit(ast.parse(source, filename=path))
        queries.extend(visitor.queries)
    return sorted(queries, key=lambda row: (row.path, row.line, row.function))


def _query_access(text: str) -> str:
    normalized = f" {' '.join(text.upper().split())} "
    if "CREATE CONSTRAINT" in normalized or "CREATE INDEX" in normalized:
        return "schema"
    if any(token in normalized for token in (" MERGE ", " CREATE ", " SET ", " DELETE ")):
        return "write"
    return "read"


def _query_records(subject_revision: str) -> list[dict[str, Any]]:
    records = []
    for index, query in enumerate(_cypher_strings(subject_revision), start=1):
        normalized = " ".join(query.text.split())
        records.append(
            {
                "query_id": f"Q{index:03d}",
                "path": query.path,
                "function": query.function,
                "subject_line": query.line,
                "access": _query_access(query.text),
                "labels": sorted(set(match[1] for match in NODE_PATTERN.findall(query.text))),
                "relationships": sorted(set(RELATIONSHIP_PATTERN.findall(query.text))),
                "normalized_sha256": sha256_bytes(normalized.encode("utf-8")),
            }
        )
    return records


def _observed_schema(subject_revision: str) -> tuple[dict[str, set[str]], set[str]]:
    labels: dict[str, set[str]] = {}
    relationships: set[str] = set()
    for query in _cypher_strings(subject_revision):
        aliases: dict[str, str] = {}
        for alias, label, properties in NODE_PATTERN.findall(query.text):
            aliases[alias] = label
            labels.setdefault(label, set())
            if properties:
                labels[label].update(re.findall(r"\b([A-Z][A-Z0-9_]*)\s*:", properties))
        for alias, label in aliases.items():
            if alias:
                labels[label].update(
                    re.findall(rf"\b{re.escape(alias)}\.([A-Z][A-Z0-9_]*)\b", query.text)
                )
        relationships.update(RELATIONSHIP_PATTERN.findall(query.text))
    return labels, relationships


NODE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "CAPTURE": {
        "purpose": "One capture/import session.",
        "identity": ["CAPTURE_ID"],
        "properties": {
            "CAPTURE_ID": "string",
            "STARTED": "neo4j datetime",
            "SOURCE": "string",
            "PACKETS": "integer",
        },
    },
    "ENDPOINT": {
        "purpose": "One computed IP behavior profile in one capture/session scope.",
        "identity": ["IP_ADDRESS", "CAPTURE_ID"],
        "properties": {
            "IP_ADDRESS": "string",
            "CAPTURE_ID": "string or null on legacy profiles",
            "EMBEDDING": "list<number>",
            "ENDPOINT_TYPE": "string",
            "ORGANIZATION": "string or null",
            "HOSTNAME": "string or null",
            "LOCATION": "string or null",
            "BYTES_OUT": "integer",
            "PACKETS_OUT": "integer",
            "OUT_PEERS": "integer",
            "OUT_PORTS": "list<integer>",
            "BYTES_IN": "integer",
            "PACKETS_IN": "integer",
            "IN_PEERS": "integer",
            "IN_PORTS": "list<integer>",
            "PROTOCOLS": "list<string>",
            "INTERVAL_MEAN": "number or null",
            "INTERVAL_CV": "number or null",
            "TIMESTAMP": "neo4j datetime",
            "OUTLIER": "boolean or absent before ranking",
        },
    },
    "IP_ADDRESS": {
        "purpose": "Observed IP address and optional enrichment metadata.",
        "identity": ["IP_ADDRESS"],
        "properties": {
            "IP_ADDRESS": "string",
            "HOSTNAME": "string",
            "LOCATION": "string",
            "COORDINATES": "string",
        },
    },
    "ORGANIZATION": {
        "purpose": "IPinfo organization label or the local-host sentinel organization.",
        "identity": ["ORGANIZATION"],
        "properties": {"ORGANIZATION": "string"},
    },
    "PACKET": {
        "purpose": "Raw packet evidence retained at 5-tuple granularity.",
        "identity": [],
        "properties": {
            "PROTOCOL": "string",
            "SIZE": "integer",
            "PAYLOAD": "string or null",
            "TIMESTAMP": "neo4j datetime",
            "CAPTURE_ID": "string",
            "SRC_IP": "string",
            "DST_IP": "string",
            "SRC_PORT": "integer",
            "DST_PORT": "integer",
        },
    },
    "PORT": {
        "purpose": "An IP-scoped nonzero transport port observed in packet evidence.",
        "identity": ["PORT", "IP_ADDRESS"],
        "properties": {"PORT": "integer", "IP_ADDRESS": "string"},
    },
}


RELATIONSHIP_DEFINITIONS = {
    "OWNERSHIP": {
        "from": "ORGANIZATION",
        "to": "IP_ADDRESS",
        "purpose": "Associates enriched or local-sentinel organization identity.",
        "properties": [],
    },
    "PORT": {
        "from": "IP_ADDRESS",
        "to": "PORT",
        "purpose": "Scopes a materialized nonzero port to an IP address.",
        "properties": [],
    },
    "PROFILE": {
        "from": "IP_ADDRESS",
        "to": "ENDPOINT",
        "purpose": "Connects an IP to each session-scoped behavior profile.",
        "properties": [],
    },
    "RECEIVED": {
        "from": "PACKET",
        "to": "PORT",
        "purpose": "Links a packet to its nonzero destination port.",
        "properties": [],
    },
    "SENT": {
        "from": "PORT",
        "to": "PACKET",
        "purpose": "Links a nonzero source port to a packet.",
        "properties": [],
    },
}


def _node_records() -> list[dict[str, Any]]:
    return [
        {
            "label": label,
            "purpose": NODE_DEFINITIONS[label]["purpose"],
            "logical_identity": NODE_DEFINITIONS[label]["identity"],
            "properties": [
                {"name": name, "observed_type": type_name}
                for name, type_name in sorted(NODE_DEFINITIONS[label]["properties"].items())
            ],
        }
        for label in sorted(NODE_DEFINITIONS)
    ]


def _relationship_records() -> list[dict[str, Any]]:
    return [
        {"type": name, **RELATIONSHIP_DEFINITIONS[name]}
        for name in sorted(RELATIONSHIP_DEFINITIONS)
    ]


CONSTRAINT_PATTERN = re.compile(
    r"CREATE CONSTRAINT ([a-z0-9_]+) IF NOT EXISTS FOR \(\w+:([A-Z_]+)\) "
    r"REQUIRE \w+\.([A-Z_]+) IS UNIQUE",
    re.IGNORECASE,
)
INDEX_PATTERN = re.compile(
    r"CREATE INDEX ([a-z0-9_]+) IF NOT EXISTS FOR \(\w+:([A-Z_]+)\) "
    r"ON \(([^)]+)\)",
    re.IGNORECASE,
)


def _schema_objects(subject_revision: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    constraints = []
    indexes = []
    for query in _cypher_strings(subject_revision):
        normalized = " ".join(query.text.split())
        constraint = CONSTRAINT_PATTERN.fullmatch(normalized)
        if constraint:
            name, label, property_name = constraint.groups()
            constraints.append(
                {
                    "name": name,
                    "kind": "node uniqueness",
                    "label": label,
                    "properties": [property_name],
                    "query_sha256": sha256_bytes(normalized.encode("utf-8")),
                }
            )
            continue
        index = INDEX_PATTERN.fullmatch(normalized)
        if index:
            name, label, expression = index.groups()
            properties = re.findall(r"\w+\.([A-Z_]+)", expression)
            indexes.append(
                {
                    "name": name,
                    "kind": "range index",
                    "label": label,
                    "properties": properties,
                    "query_sha256": sha256_bytes(normalized.encode("utf-8")),
                }
            )
    return sorted(constraints, key=lambda row: row["name"]), sorted(
        indexes, key=lambda row: row["name"]
    )


def collect_graph_schema_inventory(
    subject_revision: str,
    collector_revision: str,
) -> dict[str, Any]:
    observed_nodes, observed_relationships = _observed_schema(subject_revision)
    constraints, indexes = _schema_objects(subject_revision)
    nodes = _node_records()
    relationships = _relationship_records()
    property_count = sum(len(row["properties"]) for row in nodes)
    document = {
        "schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "artifact_kind": "neo4j_schema_inventory",
        "benchmark_id": "baseline-0",
        "subject": {
            "revision": subject_revision,
            "source_files": _source_records(subject_revision, GRAPH_SOURCE_PATHS),
        },
        "collector": _collector_record(collector_revision),
        "database": {
            "default_name": "captures",
            "role": "packet evidence, relationships, capture history, enrichment, and computed profiles",
        },
        "lifecycle": {
            "initialized_by": "jaws.jaws_utils.initialize_schema",
            "trigger": "opportunistically before each capture/import run",
            "version_record": None,
            "migration_history": None,
            "idempotence": "named constraints/indexes use IF NOT EXISTS; seed data uses MERGE",
            "failure_policy": (
                "Collect every schema-statement exception, emit a warning, and continue "
                "the capture path rather than failing atomically."
            ),
        },
        "statistics": {
            "labels": len(nodes),
            "node_properties": property_count,
            "relationship_types": len(relationships),
            "relationship_properties": 0,
            "constraints": len(constraints),
            "indexes": len(indexes),
            "cypher_source_locations": len(_cypher_strings(subject_revision)),
        },
        "nodes": nodes,
        "relationships": relationships,
        "constraints": constraints,
        "indexes": indexes,
        "seed_data": [
            {
                "name": "local-host ownership",
                "behavior": (
                    "MERGE the detected local IP, ORGANIZATION 'YOU ARE HERE', and "
                    "their OWNERSHIP relationship."
                ),
            }
        ],
        "implicit_property_joins": [
            {
                "from": "PACKET.CAPTURE_ID",
                "to": "CAPTURE.CAPTURE_ID",
                "relationship": None,
                "purpose": "session scoping without per-packet CAPTURE edges",
            },
            {
                "from": "PACKET.SRC_IP / PACKET.DST_IP",
                "to": "IP_ADDRESS.IP_ADDRESS",
                "relationship": None,
                "purpose": "packet-to-endpoint inspection and host-relative aggregation",
            },
            {
                "from": "ENDPOINT.IP_ADDRESS",
                "to": "IP_ADDRESS.IP_ADDRESS",
                "relationship": "PROFILE also exists",
                "purpose": "profile/enrichment joins and compatibility with legacy reads",
            },
        ],
        "limitations": [
            {
                "limitation_id": "GRAPH-B0-001",
                "summary": "No graph schema version node or ordered migration history exists.",
            },
            {
                "limitation_id": "GRAPH-B0-002",
                "summary": (
                    "ENDPOINT has a composite range index on (IP_ADDRESS, CAPTURE_ID) "
                    "but no composite uniqueness constraint."
                ),
            },
            {
                "limitation_id": "GRAPH-B0-003",
                "summary": (
                    "PACKET-to-CAPTURE and PACKET-to-IP referential joins are properties, "
                    "not graph relationships or enforced foreign keys."
                ),
            },
            {
                "limitation_id": "GRAPH-B0-004",
                "summary": "No property-existence, relationship, or type constraints exist.",
            },
            {
                "limitation_id": "GRAPH-B0-005",
                "summary": (
                    "Port 0 is retained on PACKET as a non-TCP/UDP placeholder but is "
                    "never materialized as a PORT node."
                ),
            },
            {
                "limitation_id": "GRAPH-B0-006",
                "summary": (
                    "Schema initialization warnings do not abort capture, so a graph may "
                    "operate with only part of the declared index/constraint set."
                ),
            },
        ],
        "source_queries": _query_records(subject_revision),
        "observed_sets": {
            "labels": sorted(observed_nodes),
            "relationships": sorted(observed_relationships),
            "properties_by_label": {
                label: sorted(properties) for label, properties in sorted(observed_nodes.items())
            },
        },
    }
    validate_graph_schema_inventory(document)
    return document


def validate_graph_schema_inventory(document: dict[str, Any]) -> None:
    if document.get("schema_version") != COMPATIBILITY_SCHEMA_VERSION:
        raise ContractViolation("unsupported graph compatibility schema version")
    if document.get("artifact_kind") != "neo4j_schema_inventory":
        raise ContractViolation("graph compatibility artifact kind is invalid")
    subject_revision = document.get("subject", {}).get("revision")
    if not subject_revision:
        raise ContractViolation("graph inventory lacks a subject revision")
    _validate_source_records(
        document["subject"].get("source_files"),
        subject_revision,
        GRAPH_SOURCE_PATHS,
        "graph",
    )
    observed_nodes, observed_relationships = _observed_schema(subject_revision)
    expected_properties = {
        label: set(definition["properties"]) for label, definition in NODE_DEFINITIONS.items()
    }
    if observed_nodes != expected_properties:
        raise ContractViolation(
            "graph node/property inventory differs from the subject Cypher; "
            f"observed={observed_nodes}, declared={expected_properties}"
        )
    if observed_relationships != set(RELATIONSHIP_DEFINITIONS):
        raise ContractViolation("graph relationship inventory differs from subject Cypher")
    if document.get("nodes") != _node_records():
        raise ContractViolation("graph declared node inventory drifted")
    if document.get("relationships") != _relationship_records():
        raise ContractViolation("graph declared relationship inventory drifted")
    if document.get("source_queries") != _query_records(subject_revision):
        raise ContractViolation("graph Cypher source inventory drifted")
    constraints, indexes = _schema_objects(subject_revision)
    if document.get("constraints") != constraints or document.get("indexes") != indexes:
        raise ContractViolation("graph constraint/index inventory drifted")
    stats = document.get("statistics", {})
    if stats != {
        "labels": 6,
        "node_properties": 40,
        "relationship_types": 5,
        "relationship_properties": 0,
        "constraints": 3,
        "indexes": 5,
        "cypher_source_locations": 44,
    }:
        raise ContractViolation(f"graph statistics drifted: {stats}")


def render_compatibility_readme(
    cli_document: dict[str, Any], graph_document: dict[str, Any]
) -> str:
    cli_cases = cli_document["cases"]
    successes = sum(row["category"] == "success" for row in cli_cases)
    handled = sum(row["category"] == "handled_error" for row in cli_cases)
    arguments = sum(row["category"] == "argument_error" for row in cli_cases)
    stats = graph_document["statistics"]
    lines = [
        "# Benchmark 0 compatibility inventories",
        "",
        "These machine-readable records complete the adapter and storage portion of Benchmark 0.",
        "They describe the frozen subject revision; they do not propose the Milestone 1 CLI",
        "or Milestone 2 graph design.",
        "",
        "## CLI JSON contract",
        "",
        f"`cli-contract.json` retains {len(cli_cases)} deterministic invocations:",
        f"{successes} successes, {handled} handled application/configuration failures,",
        f"and {arguments} argparse validation failure.",
        "",
        "| Case | Entry point | Category | Exit | Stdout |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in cli_cases:
        lines.append(
            f"| `{row['case_id']}` | `{row['entry_point']}` | "
            f"{row['category']} | {row['exit_code']} | {row['stdout_kind']} |"
        )
    lines += [
        "",
        "The observed contract deliberately records that Reporter-handled failures emit",
        "`ok=false` JSON while retaining exit status 0. Argument parsing is the exception:",
        "it emits stderr text, no JSON, and exits 2. This is baseline evidence, not an",
        "endorsement; typed CLI semantics are decided in Milestone 1.",
        "",
        "## Neo4j graph schema",
        "",
        "`neo4j-schema.json` is derived from every Cypher-bearing source location at the",
        "subject revision. It records:",
        "",
        f"- {stats['labels']} node labels and {stats['node_properties']} observed node properties",
        f"- {stats['relationship_types']} relationship types and no relationship properties",
        f"- {stats['constraints']} uniqueness constraints and {stats['indexes']} range indexes",
        f"- {stats['cypher_source_locations']} Cypher source locations with source digests",
        "- property-based joins, seed data, lifecycle behavior, and known limitations",
        "",
        "The current schema is initialized opportunistically before capture, has no schema",
        "version or migration ledger, and can continue after partial initialization errors.",
        "Those observations establish the migration boundary for Milestone 2.",
        "",
        "## Integrity",
        "",
        "Both records name the detector subject and their own committed collector revision.",
        "The root Benchmark 0 manifest inventories these files, and the root checksum file",
        "covers them. The existing bundle validator also scans them for credential patterns.",
        "",
    ]
    return "\n".join(lines)


def validate_compatibility_directory(directory: Path | str) -> None:
    directory = Path(directory)
    cli_path = directory / "cli-contract.json"
    graph_path = directory / "neo4j-schema.json"
    readme_path = directory / "README.md"
    if not all(path.is_file() for path in (cli_path, graph_path, readme_path)):
        raise ContractViolation("compatibility directory is incomplete")
    cli_document = json.loads(cli_path.read_text(encoding="utf-8"))
    graph_document = json.loads(graph_path.read_text(encoding="utf-8"))
    validate_cli_contract(cli_document)
    validate_graph_schema_inventory(graph_document)
    expected_readme = render_compatibility_readme(cli_document, graph_document)
    if readme_path.read_text(encoding="utf-8") != expected_readme:
        raise ContractViolation("compatibility README is not deterministic")
    if cli_document["subject"]["revision"] != graph_document["subject"]["revision"]:
        raise ContractViolation("compatibility subject revisions differ")
    if cli_document["collector"] != graph_document["collector"]:
        raise ContractViolation("compatibility collector records differ")
    collector = cli_document["collector"]
    expected_digest = _tree_digest_at_revision(collector["revision"], list(COLLECTOR_SOURCE_PATHS))
    if collector["source_sha256"] != expected_digest:
        raise ContractViolation("compatibility collector source digest is invalid")


def _extend_manifest(bundle: Path, subject_revision: str) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["subject"]["revision"] != subject_revision:
        raise ContractViolation("compatibility subject differs from Benchmark 0 subject")
    declared = {row["path"] for row in manifest["artifacts"]}
    for path, media_type in COMPATIBILITY_ARTIFACTS:
        if path in declared:
            raise ContractViolation(f"Benchmark 0 already declares {path}")
        manifest["artifacts"].append(
            {
                "path": path,
                "role": "report",
                "media_type": media_type,
                "required": True,
            }
        )
    manifest["artifacts"] = sorted(manifest["artifacts"], key=lambda row: row["path"])
    _write_json(manifest_path, manifest)


def collect_canonical_compatibility(
    *,
    bundle: Path | str = DEFAULT_BASELINE,
    subject_revision: str = DEFAULT_SUBJECT_REVISION,
    collector_revision: str | None = None,
) -> Path:
    if not collector_revision or collector_revision == "worktree":
        raise ContractViolation(
            "canonical compatibility collection requires a committed collector revision"
        )
    changes = _worktree_changes()
    if changes:
        raise ContractViolation(
            "canonical compatibility collection requires a clean working tree; found "
            + ", ".join(changes[:5])
        )
    bundle = Path(bundle).resolve()
    validate_bundle(bundle)
    subject_revision = _resolve_commit(subject_revision)
    collector_revision = _resolve_commit(collector_revision)
    if subject_revision != _resolve_commit(DEFAULT_SUBJECT_REVISION):
        raise ContractViolation("compatibility inventory must describe Benchmark 0 subject")
    _verify_subject_sources(
        subject_revision, sorted(set(CLI_SUBJECT_PATHS) | set(GRAPH_SOURCE_PATHS))
    )
    current_digest = _tree_digest(list(COLLECTOR_SOURCE_PATHS))
    committed_digest = _tree_digest_at_revision(collector_revision, list(COLLECTOR_SOURCE_PATHS))
    if current_digest != committed_digest:
        raise ContractViolation("compatibility collector differs from named revision")
    directory = bundle / "compatibility"
    if directory.exists():
        raise ContractViolation(f"refusing to overwrite compatibility inventory: {directory}")
    directory.mkdir()
    cli_document = collect_cli_contract(subject_revision, collector_revision)
    graph_document = collect_graph_schema_inventory(subject_revision, collector_revision)
    _write_json(directory / "cli-contract.json", cli_document)
    _write_json(directory / "neo4j-schema.json", graph_document)
    (directory / "README.md").write_text(
        render_compatibility_readme(cli_document, graph_document), encoding="utf-8"
    )
    _extend_manifest(bundle, subject_revision)
    write_checksums(bundle)
    validate_compatibility_directory(directory)
    validate_bundle(bundle)
    return directory


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and validate Benchmark 0 CLI and Neo4j compatibility records."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect")
    collect.add_argument("--bundle", type=Path, default=DEFAULT_BASELINE)
    collect.add_argument("--subject-revision", default=DEFAULT_SUBJECT_REVISION)
    collect.add_argument("--collector-revision", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("directory", type=Path, default=COMPATIBILITY_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "collect":
            path = collect_canonical_compatibility(
                bundle=args.bundle,
                subject_revision=args.subject_revision,
                collector_revision=args.collector_revision,
            )
            print(f"collected and validated {path}")
        else:
            validate_compatibility_directory(args.directory)
            print(f"validated {args.directory}")
    except ContractViolation as exc:
        print(f"compatibility contract error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
