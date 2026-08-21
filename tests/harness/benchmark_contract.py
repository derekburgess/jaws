"""Versioned Benchmark 0 bundle collection, validation, and report rendering.

This module is a flight recorder around the existing detector. It deliberately calls
the same profile and score functions as the recall harness and does not alter detector
features, thresholds, labels, or ordering.

Run from the repository root with PYTHONPATH=tests:

    python -m harness.benchmark_contract collect-example
    python -m harness.benchmark_contract collect-baseline --collector-revision <sha>
    python -m harness.benchmark_contract validate benchmarks/examples/baseline-0
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import psutil
from jsonschema import Draft202012Validator, FormatChecker

# The legacy finder imports Matplotlib even though this collector never renders plots.
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "jaws-matplotlib"))

from jaws.jaws_finder import (
    MIN_BASELINE_SESSIONS,
    NUMERIC_FEATURE_NAMES,
    REASON_Z_THRESHOLD,
    SCORE_TOP_K,
    build_numeric_features,
    score_endpoints,
    score_host_outbound,
)

from .pcap import NJRAT, available, documented_pcap_scenarios, pcap_dir
from .recall import _history, _host_outbound_rows, _profiles, report
from .scenarios import CAPTURE, HOST, SCENARIOS, WINDOW, background_packets

SCHEMA_VERSION = "1.0.0"
BENCHMARK_ID = "baseline-0"
DEFAULT_SUBJECT_REVISION = "0b68a8c"
DEFAULT_SEED = 7
CUTOFF = 3

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SOURCE = REPO_ROOT / "benchmarks" / "schemas" / BENCHMARK_ID
DEFAULT_EXAMPLE = REPO_ROOT / "benchmarks" / "examples" / BENCHMARK_ID
DEFAULT_BASELINE = REPO_ROOT / "benchmarks" / BENCHMARK_ID

SCHEMA_FILES = {
    "manifest": "manifest.schema.json",
    "dataset_catalog": "dataset.schema.json",
    "scenario_catalog": "scenario.schema.json",
    "run": "run.schema.json",
    "environment": "environment.schema.json",
    "ranking": "ranking.schema.json",
    "evaluation": "evaluation.schema.json",
    "known_failures": "known-failures.schema.json",
}

DOCUMENT_FILES = {
    "manifest": "manifest.json",
    "dataset_catalog": "datasets.json",
    "scenario_catalog": "scenarios.json",
    "run": "run.json",
    "environment": "environment.json",
    "evaluation": "evaluation.json",
    "known_failures": "known-failures.json",
}

SAFE_ENVIRONMENT_NAMES = (
    "OPENAI_API_KEY",
    "NEO4J_PASSWORD",
    "IPINFO_TOKEN",
    "HF_TOKEN",
    "JAWS_PCAP_DIR",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:password|api[_-]?key|access[_-]?token)\s*[:=]\s*"
        r"[\"']?(?!redacted\b|unset\b)[^\s,\"']{8,}"
    ),
    re.compile(r"(?i)\b(?:neo4j|bolt|https?)://[^/\s:@]+:[^@\s/]+@"),
)

KNOWN_FAILURES = (
    {
        "failure_id": "BF0-KF-001",
        "title": "Bare TCP keepalive cadence crowds the endpoint ranking",
        "status": "open",
        "scenario_ids": ["tcp_keepalive"],
        "evaluation_surface": "endpoints",
        "first_observed_revision": DEFAULT_SUBJECT_REVISION,
        "expected_rule": "A benign bare-ACK keepalive ranks below position 3.",
        "observed_behavior": "The metronomic low-payload exchange ranks in the top three.",
        "rationale": (
            "Cadence alone is structurally beacon-like; Benchmark 0 preserves the false "
            "positive until payload-aware research is evaluated."
        ),
        "regression_guard": (
            "A later fix must demote tcp_keepalive without reducing payload_beacon or "
            "jittered_beacon recall at the declared cutoff."
        ),
    },
    {
        "failure_id": "BF0-KF-002",
        "title": "Bulk download crowds the host-outbound ranking",
        "status": "open",
        "scenario_ids": ["bulk_download"],
        "evaluation_surface": "host_outbound",
        "first_observed_revision": DEFAULT_SUBJECT_REVISION,
        "expected_rule": "A predominantly inbound transfer ranks below position 3.",
        "observed_behavior": "The small request side of a large download ranks in the top three.",
        "rationale": (
            "The current host-outbound score is population-relative and still overweights "
            "the upload magnitude associated with a large inbound transfer."
        ),
        "regression_guard": (
            "A later fix must demote bulk_download without reducing slow_exfil or "
            "burst_exfil recall at the declared cutoff."
        ),
    },
    {
        "failure_id": "BF0-KF-003",
        "title": "Historically stable heavy traffic remains highly ranked",
        "status": "open",
        "scenario_ids": ["stable_heavy"],
        "evaluation_surface": "endpoints",
        "first_observed_revision": DEFAULT_SUBJECT_REVISION,
        "expected_rule": "A consistently heavy endpoint ranks below position 3.",
        "observed_behavior": "The endpoint remains in the top three despite three prior sessions.",
        "rationale": (
            "Historical normalization reduces persistent-volume dominance but does not yet "
            "remove every peer-relative or cadence contribution."
        ),
        "regression_guard": (
            "A later fix must demote stable_heavy while behavioral_change remains detectable."
        ),
    },
)


class ContractViolation(ValueError):
    """Raised when a bundle violates its schema, integrity, or cross-record contract."""


def utc_now() -> str:
    """RFC3339 UTC timestamp with millisecond precision."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    """Stable JSON bytes used for evidence and source-tree digests."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_digest(paths: Iterable[Path]) -> str:
    entries = []
    for path in sorted({Path(p).resolve() for p in paths}):
        entries.append(
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": sha256_file(path),
            }
        )
    return sha256_bytes(canonical_json_bytes(entries))


def _git_file(revision: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{relative}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractViolation(f"cannot read {relative} at revision {revision}") from exc
    return result.stdout


def _resolve_commit(revision: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractViolation(f"cannot resolve git revision {revision!r}") from exc
    resolved = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", resolved):
        raise ContractViolation(f"git revision {revision!r} did not resolve to a full commit SHA")
    return resolved


def _tree_digest_at_revision(revision: str, paths: Iterable[Path]) -> str:
    entries = []
    for path in sorted({Path(p).resolve() for p in paths}):
        relative = path.relative_to(REPO_ROOT).as_posix()
        entries.append(
            {
                "path": relative,
                "sha256": sha256_bytes(_git_file(revision, relative)),
            }
        )
    return sha256_bytes(canonical_json_bytes(entries))


def _worktree_changes() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractViolation("cannot inspect the collector working tree") from exc
    return [line for line in result.stdout.splitlines() if line]


def _collector_source_paths() -> list[Path]:
    return [
        Path(__file__),
        Path(__file__).with_name("__init__.py"),
        Path(__file__).with_name("recall.py"),
        Path(__file__).with_name("scenarios.py"),
        Path(__file__).with_name("pcap.py"),
        *SCHEMA_SOURCE.glob("*.schema.json"),
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractViolation(f"{path}: cannot read valid JSON: {exc}") from exc


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(canonical_json_bytes(row).decode("utf-8") + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ContractViolation(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ContractViolation(f"{path}: cannot read JSONL: {exc}") from exc
    return rows


def _schema_error(prefix: str, error: Any) -> str:
    location = "/".join(str(part) for part in error.absolute_path) or "<root>"
    return f"{prefix}:{location}: {error.message}"


def _load_bundle_schemas(bundle: Path) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for kind, filename in SCHEMA_FILES.items():
        schema_path = bundle / "schemas" / filename
        schema = _read_json(schema_path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            raise ContractViolation(f"{schema_path}: invalid JSON Schema: {exc}") from exc
        canonical_path = SCHEMA_SOURCE / filename
        if not canonical_path.is_file():
            raise ContractViolation(f"canonical schema is missing: {canonical_path}")
        if sha256_file(schema_path) != sha256_file(canonical_path):
            raise ContractViolation(
                f"{schema_path}: bundled schema differs from the repository schema"
            )
        schemas[kind] = schema
    return schemas


def _validate_instance(instance: Any, schema: dict[str, Any], label: str) -> None:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        rendered = "; ".join(_schema_error(label, error) for error in errors[:8])
        raise ContractViolation(rendered)


def write_checksums(bundle: Path) -> None:
    """Write a complete sha256sum-compatible inventory, excluding itself."""
    checksum_path = bundle / "checksums.sha256"
    paths = sorted(path for path in bundle.rglob("*") if path.is_file() and path != checksum_path)
    lines = [f"{sha256_file(path)}  {path.relative_to(bundle).as_posix()}" for path in paths]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_checksums(bundle: Path) -> dict[str, str]:
    path = bundle / "checksums.sha256"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ContractViolation(f"{path}: cannot read checksum inventory: {exc}") from exc
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/-]+)", line)
        if not match:
            raise ContractViolation(f"{path}:{line_number}: malformed checksum line")
        digest, relative = match.groups()
        if relative in checksums:
            raise ContractViolation(f"{path}: duplicate checksum path {relative!r}")
        checksums[relative] = digest
    return checksums


def _validate_checksums(bundle: Path) -> None:
    declared = _read_checksums(bundle)
    actual_paths = {
        path.relative_to(bundle).as_posix(): path
        for path in bundle.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    }
    if set(declared) != set(actual_paths):
        missing = sorted(set(actual_paths) - set(declared))
        extra = sorted(set(declared) - set(actual_paths))
        raise ContractViolation(f"checksum inventory mismatch; missing={missing}, extra={extra}")
    for relative, path in actual_paths.items():
        actual = sha256_file(path)
        if actual != declared[relative]:
            raise ContractViolation(
                f"checksum mismatch for {relative}: expected {declared[relative]}, got {actual}"
            )


def _scan_for_secrets(bundle: Path) -> None:
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == "checksums.sha256":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise ContractViolation(
                    f"potential secret material in {path.relative_to(bundle)}: "
                    f"matched {pattern.pattern!r}"
                )


def _assert_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ContractViolation(f"duplicate {label}: {sorted(duplicates)}")


def _passed(expectation: str, rank: int, cutoff: int = CUTOFF) -> bool:
    return rank <= cutoff if expectation == "detect" else rank > cutoff


def _known_failure_map(
    known_failures: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_id = {failure["failure_id"]: failure for failure in known_failures["failures"]}
    by_scenario = {
        scenario_id: failure
        for failure in known_failures["failures"]
        for scenario_id in failure["scenario_ids"]
    }
    return by_id, by_scenario


def build_evaluation(
    scenarios: dict[str, Any],
    rankings: list[dict[str, Any]],
    known_failures: dict[str, Any],
    cutoff: int = CUTOFF,
) -> dict[str, Any]:
    """Calculate the deterministic reward vector from retained rankings."""
    scenario_map = {row["scenario_id"]: row for row in scenarios["scenarios"]}
    ranking_map = {row["scenario_id"]: row for row in rankings}
    _, known_by_scenario = _known_failure_map(known_failures)
    per_scenario = []

    for scenario_id in scenario_map:
        scenario = scenario_map[scenario_id]
        ranking = ranking_map[scenario_id]
        execution = ranking["execution_status"]
        if execution != "completed":
            per_scenario.append(
                {
                    "scenario_id": scenario_id,
                    "execution_status": execution,
                    "quality_outcome": "not_evaluated",
                    "expectation": scenario["expectation"],
                    "evaluation_surface": scenario["evaluation_surface"],
                    "target_rank": None,
                    "candidate_count": None,
                    "passed": None,
                    "known_failure_id": None,
                    "metrics": [],
                    "rationale": ranking["skip_reason"]
                    or ranking["error"]
                    or "Execution did not complete.",
                }
            )
            continue

        rank = ranking["target"]["rank"]
        passed = _passed(scenario["expectation"], rank, cutoff)
        known = known_by_scenario.get(scenario_id)
        quality = "passed" if passed else ("failed_known" if known else "failed_unexpected")
        reciprocal = round(1.0 / rank, 6)
        per_scenario.append(
            {
                "scenario_id": scenario_id,
                "execution_status": execution,
                "quality_outcome": quality,
                "expectation": scenario["expectation"],
                "evaluation_surface": scenario["evaluation_surface"],
                "target_rank": rank,
                "candidate_count": len(ranking["findings"]),
                "passed": passed,
                "known_failure_id": known["failure_id"] if quality == "failed_known" else None,
                "metrics": [
                    {
                        "name": "target_rank",
                        "value": rank,
                        "unit": "one-based-rank",
                        "preferred_direction": (
                            "lower" if scenario["expectation"] == "detect" else "higher"
                        ),
                    },
                    {
                        "name": "reciprocal_rank",
                        "value": reciprocal,
                        "unit": "ratio",
                        "preferred_direction": (
                            "higher" if scenario["expectation"] == "detect" else "lower"
                        ),
                    },
                    {
                        "name": "runtime_ms",
                        "value": ranking["duration_ms"],
                        "unit": "milliseconds",
                        "preferred_direction": "lower",
                    },
                ],
                "rationale": (
                    f"Target rank {rank} {'satisfies' if passed else 'violates'} "
                    f"{scenario['success_rule']['operator']} {cutoff}."
                ),
            }
        )

    completed = [row for row in per_scenario if row["execution_status"] == "completed"]
    detects = [row for row in completed if row["expectation"] == "detect"]
    rejects = [row for row in completed if row["expectation"] == "reject"]
    hits = sum(bool(row["passed"]) for row in detects)
    false_positives = sum(not bool(row["passed"]) for row in rejects)
    mrr = (
        round(sum(1.0 / row["target_rank"] for row in detects) / len(detects), 6)
        if detects
        else None
    )
    recall = round(hits / len(detects), 6) if detects else None
    coverage = round(len(completed) / len(per_scenario), 6) if per_scenario else None
    known_reproduced = sorted(
        row["known_failure_id"] for row in completed if row["quality_outcome"] == "failed_known"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "evaluation",
        "evaluator": {
            "name": "jaws-baseline-0-evaluator",
            "version": SCHEMA_VERSION,
            "deterministic": True,
        },
        "cutoff": cutoff,
        "per_scenario": per_scenario,
        "aggregate": {
            "scenario_count": len(per_scenario),
            "completed_count": len(completed),
            "skipped_count": sum(row["execution_status"] == "skipped" for row in per_scenario),
            "failed_execution_count": sum(
                row["execution_status"] == "failed" for row in per_scenario
            ),
            "detect": {
                "completed": len(detects),
                "hits_at_cutoff": hits,
            },
            "reject": {
                "completed": len(rejects),
                "false_positives_at_cutoff": false_positives,
            },
            "metrics": [
                {
                    "name": f"recall_at_{cutoff}",
                    "value": recall,
                    "unit": "ratio",
                    "preferred_direction": "higher",
                },
                {
                    "name": "mean_reciprocal_rank",
                    "value": mrr,
                    "unit": "ratio",
                    "preferred_direction": "higher",
                },
                {
                    "name": f"benign_top_{cutoff}_burden",
                    "value": false_positives,
                    "unit": "scenario-count",
                    "preferred_direction": "lower",
                },
                {
                    "name": "scenario_coverage",
                    "value": coverage,
                    "unit": "ratio",
                    "preferred_direction": "higher",
                },
                {
                    "name": "total_scenario_runtime_ms",
                    "value": round(
                        sum(ranking_map[row["scenario_id"]]["duration_ms"] for row in completed), 3
                    ),
                    "unit": "milliseconds",
                    "preferred_direction": "lower",
                },
            ],
            "known_failures_reproduced": known_reproduced,
        },
    }


def _validate_provenance(
    manifest: dict[str, Any],
    run: dict[str, Any],
) -> None:
    canonical = manifest["benchmark"]["canonical"]
    if not canonical:
        return
    subject_revision = manifest["subject"]["revision"]
    collector_revision = manifest["collector"]["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", subject_revision):
        raise ContractViolation("canonical subject revision must be a full commit SHA")
    if not re.fullmatch(r"[0-9a-f]{40}", collector_revision):
        raise ContractViolation("canonical collector revision must be a full commit SHA")
    if manifest["collector"]["working_tree_dirty"]:
        raise ContractViolation("canonical collector cannot have a dirty working tree")
    expected_subject = _resolve_commit(DEFAULT_SUBJECT_REVISION)
    if subject_revision != expected_subject:
        raise ContractViolation("canonical Benchmark 0 identifies the wrong detector subject")
    for record in manifest["subject"]["detector_files"]:
        expected = sha256_bytes(_git_file(subject_revision, record["path"]))
        if record["sha256"] != expected:
            raise ContractViolation(f"canonical subject digest differs for {record['path']}")
    expected_collector = _tree_digest_at_revision(collector_revision, _collector_source_paths())
    if manifest["collector"]["source_sha256"] != expected_collector:
        raise ContractViolation("canonical collector digest differs from its revision")
    command_ids = [row["command_id"] for row in manifest["commands"]]
    if command_ids != ["collect-baseline"]:
        raise ContractViolation("canonical manifest must record the collect-baseline command")
    if run["run_id"] != "baseline-0-canonical":
        raise ContractViolation("canonical run has the wrong run_id")
    if "collect-baseline" not in run["invocation"]["argv"]:
        raise ContractViolation("canonical run invocation does not name collect-baseline")


def _validate_cross_records(
    manifest: dict[str, Any],
    datasets: dict[str, Any],
    scenarios: dict[str, Any],
    run: dict[str, Any],
    rankings: list[dict[str, Any]],
    evaluation: dict[str, Any],
    known_failures: dict[str, Any],
    bundle: Path,
) -> None:
    _validate_provenance(manifest, run)
    dataset_ids = [row["dataset_id"] for row in datasets["datasets"]]
    scenario_ids = [row["scenario_id"] for row in scenarios["scenarios"]]
    ranking_ids = [row["scenario_id"] for row in rankings]
    run_ids = [row["scenario_id"] for row in run["scenario_statuses"]]
    evaluation_ids = [row["scenario_id"] for row in evaluation["per_scenario"]]
    _assert_unique(dataset_ids, "dataset IDs")
    _assert_unique(scenario_ids, "scenario IDs")
    _assert_unique(ranking_ids, "ranking scenario IDs")
    _assert_unique(run_ids, "run scenario IDs")
    _assert_unique(evaluation_ids, "evaluation scenario IDs")

    scenario_set = set(scenario_ids)
    for label, ids in (
        ("rankings", ranking_ids),
        ("run statuses", run_ids),
        ("evaluations", evaluation_ids),
    ):
        if set(ids) != scenario_set:
            raise ContractViolation(
                f"{label} do not cover the scenario catalog; "
                f"missing={sorted(scenario_set - set(ids))}, "
                f"extra={sorted(set(ids) - scenario_set)}"
            )
    if any(row["dataset_id"] not in dataset_ids for row in scenarios["scenarios"]):
        raise ContractViolation("a scenario references an unknown dataset")
    dataset_map = {row["dataset_id"]: row for row in datasets["datasets"]}
    for scenario in scenarios["scenarios"]:
        history = scenario["history"]
        expected_count = history["prior_sessions"]
        if len(history["scales"]) != expected_count or len(history["sessions"]) != expected_count:
            raise ContractViolation(
                f"{scenario['scenario_id']}: history count, scales, and sessions disagree"
            )
        expected_operator = "<=" if scenario["expectation"] == "detect" else ">"
        if (
            scenario["success_rule"]["operator"] != expected_operator
            or scenario["success_rule"]["threshold"] != evaluation["cutoff"]
        ):
            raise ContractViolation(
                f"{scenario['scenario_id']}: success rule and evaluator cutoff disagree"
            )

    scenario_map = {row["scenario_id"]: row for row in scenarios["scenarios"]}
    run_map = {row["scenario_id"]: row for row in run["scenario_statuses"]}
    evaluation_map = {row["scenario_id"]: row for row in evaluation["per_scenario"]}
    known_by_id, known_by_scenario = _known_failure_map(known_failures)
    _assert_unique(known_by_id, "known-failure IDs")
    known_scenario_ids = [
        scenario_id
        for failure in known_failures["failures"]
        for scenario_id in failure["scenario_ids"]
    ]
    _assert_unique(known_scenario_ids, "known-failure scenario assignments")
    if not set(known_scenario_ids) <= scenario_set:
        raise ContractViolation("a known failure references an unknown scenario")
    for failure in known_failures["failures"]:
        for scenario_id in failure["scenario_ids"]:
            if failure["evaluation_surface"] != scenario_map[scenario_id]["evaluation_surface"]:
                raise ContractViolation(
                    f"{failure['failure_id']}: surface differs from {scenario_id}"
                )

    for ranking in rankings:
        scenario = scenario_map[ranking["scenario_id"]]
        status = run_map[ranking["scenario_id"]]
        observed = evaluation_map[ranking["scenario_id"]]
        for field in ("execution_status", "quality_outcome"):
            values = {ranking[field], status[field], observed[field]}
            if len(values) != 1:
                raise ContractViolation(
                    f"{ranking['scenario_id']}: inconsistent {field}: {sorted(values)}"
                )
        if ranking["duration_ms"] != status["duration_ms"]:
            raise ContractViolation(f"{ranking['scenario_id']}: run and ranking durations disagree")
        if ranking["evaluation_surface"] != scenario["evaluation_surface"]:
            raise ContractViolation(
                f"{ranking['scenario_id']}: ranking surface differs from scenario"
            )
        if ranking["expected_behavior"] != scenario["expectation"]:
            raise ContractViolation(f"{ranking['scenario_id']}: expectation differs from scenario")
        if ranking["evidence"]["packet_rows_sha256"] != scenario["packet_rows_sha256"]:
            raise ContractViolation(
                f"{ranking['scenario_id']}: evidence digest differs from scenario"
            )
        if ranking["evidence"]["packet_count"] != scenario["packet_count"]:
            raise ContractViolation(f"{ranking['scenario_id']}: packet count differs from scenario")
        dataset = dataset_map[ranking["dataset_id"]]
        if dataset["availability"] != "available" and ranking["execution_status"] == "completed":
            raise ContractViolation(
                f"{ranking['scenario_id']}: completed against unavailable evidence"
            )

        if ranking["execution_status"] != "completed":
            continue
        findings = ranking["findings"]
        ranks = [row["rank"] for row in findings]
        positions = [row["emitted_position"] for row in findings]
        if ranks != list(range(1, len(findings) + 1)):
            raise ContractViolation(
                f"{ranking['scenario_id']}: ranks must be contiguous and one-based"
            )
        if positions != list(range(len(findings))):
            raise ContractViolation(
                f"{ranking['scenario_id']}: emitted positions must be contiguous and zero-based"
            )
        scores = [row["score"]["value"] for row in findings]
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ContractViolation(
                f"{ranking['scenario_id']}: scores are not in detector emission order"
            )
        entities = [row["entity"]["entity_id"] for row in findings]
        _assert_unique(entities, f"{ranking['scenario_id']} finding entities")
        target_id = ranking["target"]["entity"]["entity_id"]
        if target_id not in entities:
            raise ContractViolation(
                f"{ranking['scenario_id']}: target is absent from a completed ranking"
            )
        target = findings[entities.index(target_id)]
        if ranking["target"]["rank"] != target["rank"]:
            raise ContractViolation(f"{ranking['scenario_id']}: target rank does not match finding")
        if ranking["target"]["score"] != target["score"]:
            raise ContractViolation(
                f"{ranking['scenario_id']}: target score does not match finding"
            )
        if observed["target_rank"] != target["rank"]:
            raise ContractViolation(
                f"{ranking['scenario_id']}: evaluation target rank does not match ranking"
            )
        for finding in findings:
            evidence = finding["evidence"]
            if (
                evidence["scenario_id"] != ranking["scenario_id"]
                or evidence["dataset_id"] != ranking["dataset_id"]
                or evidence["packet_rows_sha256"] != ranking["evidence"]["packet_rows_sha256"]
            ):
                raise ContractViolation(
                    f"{ranking['scenario_id']}: finding evidence pointer is inconsistent"
                )
            expected_score_field = ranking["score_semantics"]["field"]
            if finding["score"]["name"] != expected_score_field:
                raise ContractViolation(
                    f"{ranking['scenario_id']}: finding score field is inconsistent"
                )
            attributes = finding["attributes"]
            for reason in finding["reasons"]:
                if ranking["evaluation_surface"] == "endpoints":
                    raw_value = attributes["numeric_features"][reason["feature"]]
                else:
                    raw_value = attributes[reason["feature"]]
                if abs(raw_value - reason["value"]) > 0.0001:
                    raise ContractViolation(
                        f"{ranking['scenario_id']}: reason value does not match retained "
                        f"feature {reason['feature']}"
                    )
        if observed["quality_outcome"] == "failed_known":
            failure_id = observed["known_failure_id"]
            if failure_id not in known_by_id:
                raise ContractViolation(
                    f"{ranking['scenario_id']}: unknown failure ID {failure_id}"
                )
            if known_by_scenario.get(ranking["scenario_id"]) != known_by_id[failure_id]:
                raise ContractViolation(
                    f"{ranking['scenario_id']}: known failure does not name scenario"
                )

    expected_evaluation = build_evaluation(
        scenarios, rankings, known_failures, evaluation["cutoff"]
    )
    if canonical_json_bytes(evaluation) != canonical_json_bytes(expected_evaluation):
        raise ContractViolation(
            "evaluation.json is not the deterministic evaluation of retained rankings"
        )

    declared_artifacts = [row["path"] for row in manifest["artifacts"]]
    _assert_unique(declared_artifacts, "manifest artifact paths")
    actual_artifacts = sorted(
        path.relative_to(bundle).as_posix() for path in bundle.rglob("*") if path.is_file()
    )
    if sorted(declared_artifacts) != actual_artifacts:
        raise ContractViolation(
            "manifest artifact inventory differs from bundle contents; "
            f"declared_only={sorted(set(declared_artifacts) - set(actual_artifacts))}, "
            f"actual_only={sorted(set(actual_artifacts) - set(declared_artifacts))}"
        )

    schema_catalog = {row["artifact_kind"]: row for row in manifest["schema_catalog"]}
    if set(schema_catalog) != set(SCHEMA_FILES):
        raise ContractViolation("manifest schema catalog is incomplete")
    for kind, filename in SCHEMA_FILES.items():
        if schema_catalog[kind]["path"] != f"schemas/{filename}":
            raise ContractViolation(f"manifest schema path is wrong for {kind}")


def validate_bundle(bundle: Path | str) -> None:
    """Validate schemas, records, references, report, secrets, and checksums."""
    bundle = Path(bundle).resolve()
    if not bundle.is_dir():
        raise ContractViolation(f"bundle directory does not exist: {bundle}")
    schemas = _load_bundle_schemas(bundle)
    documents = {kind: _read_json(bundle / filename) for kind, filename in DOCUMENT_FILES.items()}
    rankings = _read_jsonl(bundle / "rankings.jsonl")
    for kind, document in documents.items():
        _validate_instance(document, schemas[kind], DOCUMENT_FILES[kind])
    for index, ranking in enumerate(rankings, start=1):
        _validate_instance(ranking, schemas["ranking"], f"rankings.jsonl:{index}")
    _validate_cross_records(
        documents["manifest"],
        documents["dataset_catalog"],
        documents["scenario_catalog"],
        documents["run"],
        rankings,
        documents["evaluation"],
        documents["known_failures"],
        bundle,
    )
    expected_report = render_report(bundle)
    actual_report = (bundle / "report.md").read_text(encoding="utf-8")
    if actual_report != expected_report:
        raise ContractViolation(
            "report.md is not the deterministic rendering of machine-readable records"
        )
    _scan_for_secrets(bundle)
    _validate_checksums(bundle)


def _source_digest(function: Any) -> str:
    try:
        source = inspect.getsource(function)
    except (OSError, TypeError):
        source = repr(function)
    return sha256_bytes(source.encode("utf-8"))


def _generator_seed(function: Any) -> int | None:
    parameter = inspect.signature(function).parameters.get("seed")
    if parameter is None or parameter.default is inspect.Parameter.empty:
        return None
    return int(parameter.default)


def _entity_type(surface: str) -> str:
    return "endpoint-ip" if surface == "endpoints" else "host-destination"


def _scenario_catalog_record(
    scenario: Any,
    dataset_id: str,
    source_type: str,
    packet_rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    packet_digest = (
        sha256_bytes(canonical_json_bytes(packet_rows)) if packet_rows is not None else None
    )
    if source_type == "synthetic":
        parameters = {
            "t0": 0.0,
            "scale": 1.0,
            "seed": _generator_seed(scenario.generator),
        }
    else:
        parameters = {
            "filename": NJRAT.filename,
            "local_ip": NJRAT.local_ip,
            "scale": 1.0,
            "t0": 0.0,
        }
    return {
        "scenario_id": scenario.name,
        "scenario_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "source_type": source_type,
        "generator": {
            "qualified_name": (
                f"{scenario.generator.__module__}.{scenario.generator.__qualname__}"
            ),
            "source_sha256": _source_digest(scenario.generator),
            "parameters": parameters,
        },
        "packet_rows_sha256": packet_digest,
        "packet_count": len(packet_rows) if packet_rows is not None else None,
        "observation_window": {
            "capture_id": CAPTURE,
            "start_offset_seconds": 0.0,
            "duration_seconds": WINDOW,
            "host_perspective": {
                "entity_type": "endpoint-ip",
                "entity_id": HOST,
            },
        },
        "target": {
            "entity_type": _entity_type(scenario.surface),
            "entity_id": scenario.planted_ip,
        },
        "expectation": scenario.expect,
        "evaluation_surface": scenario.surface,
        "seeds": {
            "background": DEFAULT_SEED if source_type == "synthetic" else None,
            "scenario": (
                _generator_seed(scenario.generator) if source_type == "synthetic" else None
            ),
        },
        "history": {
            "prior_sessions": len(scenario.prior_scales),
            "scales": list(scenario.prior_scales),
            "background_seed_rule": (
                "seed + zero-based prior-session index" if scenario.prior_scales else None
            ),
            "sessions": _prior_history_records(scenario),
        },
        "success_rule": {
            "metric": "target_rank",
            "operator": "<=" if scenario.expect == "detect" else ">",
            "threshold": CUTOFF,
            "rank_base": 1,
        },
        "note": scenario.note,
    }


def _prior_history_records(scenario: Any) -> list[dict[str, Any]]:
    records = []
    for index, scale in enumerate(scenario.prior_scales):
        t0 = -WINDOW * (len(scenario.prior_scales) - index) - 3600.0
        rows = background_packets(t0=t0, seed=DEFAULT_SEED + index)
        rows += scenario.packets(t0=t0, scale=scale)
        capture_id = f"PRIOR{index}"
        for row in rows:
            row["capture_id"] = capture_id
        records.append(
            {
                "capture_id": capture_id,
                "start_offset_seconds": t0,
                "scale": float(scale),
                "background_seed": DEFAULT_SEED + index,
                "packet_count": len(rows),
                "packet_rows_sha256": sha256_bytes(canonical_json_bytes(rows)),
            }
        )
    return records


def _normalize_reason(reason: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature": reason["feature"],
        "value": float(reason["value"]),
        "unit": reason["unit"],
        "robust_z": float(reason["robust_z"]),
        "direction": reason["direction"],
        "compared_to": reason.get("compared_to"),
        "baseline": (float(reason["baseline"]) if reason.get("baseline") is not None else None),
        "baseline_sessions": (
            int(reason["baseline_sessions"])
            if reason.get("baseline_sessions") is not None
            else None
        ),
        "host_relative": reason.get("host_relative"),
    }


def _normalize_finding(
    finding: dict[str, Any],
    position: int,
    scenario_record: dict[str, Any],
    score_field: str,
    endpoint_context: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    surface = scenario_record["evaluation_surface"]
    if surface == "endpoints":
        context = endpoint_context[finding["ip_address"]]
        profile = context["profile"]
        attributes = {
            "endpoint_type": finding.get("endpoint_type"),
            "org": finding["org"],
            "cloud_hosted": bool(finding["cloud_hosted"]),
            "hostname": finding["hostname"],
            "location": finding["location"],
            "bytes_out": int(finding["bytes_out"]),
            "packets_out": int(finding["packets_out"]),
            "bytes_in": int(finding["bytes_in"]),
            "packets_in": int(finding["packets_in"]),
            "out_peers": int(profile["out_peers"]),
            "in_peers": int(profile["in_peers"]),
            "out_ports": [int(port) for port in profile["out_ports"]],
            "in_ports": [int(port) for port in profile["in_ports"]],
            "protocols": [str(protocol) for protocol in profile["protocols"]],
            "interval_mean": (
                float(finding["interval_mean"]) if finding["interval_mean"] is not None else None
            ),
            "interval_cv": (
                float(finding["interval_cv"]) if finding["interval_cv"] is not None else None
            ),
            "baseline_sessions": int(finding["baseline_sessions"]),
            "first_seen": finding["first_seen"],
            "numeric_features": {
                name: float(context["numeric_features"][name]) for name in NUMERIC_FEATURE_NAMES
            },
        }
        verdict = {"name": "is_outlier", "value": bool(finding["is_outlier"])}
    else:
        attributes = {
            "org": finding["org"],
            "cloud_hosted": bool(finding["cloud_hosted"]),
            "hostname": finding["hostname"],
            "location": finding["location"],
            "upload_bytes": int(finding["upload_bytes"]),
            "upload_packets": int(finding["upload_packets"]),
            "download_bytes": int(finding["download_bytes"]),
            "download_packets": int(finding["download_packets"]),
            "upload_download_ratio": float(finding["upload_download_ratio"]),
        }
        verdict = {"name": "is_flagged", "value": bool(finding["is_flagged"])}
    return {
        "rank": position + 1,
        "emitted_position": position,
        "entity": {
            "entity_type": _entity_type(surface),
            "entity_id": finding["ip_address"],
        },
        "score": {
            "name": score_field,
            "value": float(finding[score_field]),
        },
        "verdict": verdict,
        "attributes": attributes,
        "reasons": [_normalize_reason(reason) for reason in finding["reasons"]],
        "evidence": {
            "dataset_id": scenario_record["dataset_id"],
            "scenario_id": scenario_record["scenario_id"],
            "capture_id": scenario_record["observation_window"]["capture_id"],
            "packet_rows_sha256": scenario_record["packet_rows_sha256"],
        },
    }


def _completed_ranking(
    scenario: Any,
    scenario_record: dict[str, Any],
    packet_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    start_clock = time.perf_counter()
    if scenario.surface == "host_outbound":
        detector_rows = score_host_outbound(_host_outbound_rows(packet_rows))
        score_field = "outbound_score"
        endpoint_context = None
    else:
        profiles = _profiles(packet_rows)
        numeric_matrix = build_numeric_features(profiles)
        endpoint_context = {
            profile["ip_address"]: {
                "profile": profile,
                "numeric_features": {
                    name: float(numeric_matrix[index, column])
                    for column, name in enumerate(NUMERIC_FEATURE_NAMES)
                },
            }
            for index, profile in enumerate(profiles)
        }
        detector_rows = score_endpoints(
            profiles,
            np.zeros(len(profiles), dtype=int),
            _history(scenario, DEFAULT_SEED),
        )
        score_field = "anomaly_score"

    findings = [
        _normalize_finding(row, position, scenario_record, score_field, endpoint_context)
        for position, row in enumerate(detector_rows)
    ]
    target = next(
        (row for row in findings if row["entity"]["entity_id"] == scenario.planted_ip),
        None,
    )
    if target is None:
        raise ContractViolation(f"{scenario.name}: planted entity is absent from detector ranking")
    passed = _passed(scenario.expect, target["rank"])
    known = next(
        (row for row in KNOWN_FAILURES if scenario.name in row["scenario_ids"]),
        None,
    )
    outcome = "passed" if passed else ("failed_known" if known else "failed_unexpected")
    duration_ms = round((time.perf_counter() - start_clock) * 1000.0, 3)
    ranking = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "ranking",
        "scenario_id": scenario.name,
        "scenario_version": SCHEMA_VERSION,
        "dataset_id": scenario_record["dataset_id"],
        "evaluation_surface": scenario.surface,
        "analytical_mode": {
            "name": "numeric-only",
            "state": "used",
        },
        "execution_status": "completed",
        "duration_ms": duration_ms,
        "quality_outcome": outcome,
        "expected_behavior": scenario.expect,
        "target": {
            "entity": scenario_record["target"],
            "rank": target["rank"],
            "score": target["score"],
        },
        "evidence": {
            "capture_id": scenario_record["observation_window"]["capture_id"],
            "packet_rows_sha256": scenario_record["packet_rows_sha256"],
            "packet_count": scenario_record["packet_count"],
        },
        "score_semantics": {
            "field": score_field,
            "order": "descending",
            "tie_behavior": "preserve-detector-emission-order",
        },
        "detector_parameters": {
            "cluster_labels": {
                "state": "provided" if scenario.surface == "endpoints" else "not_applicable",
                "value": 0 if scenario.surface == "endpoints" else None,
            },
            "historical_baseline": {
                "state": (
                    "used"
                    if scenario.prior_scales
                    else "not_used"
                    if scenario.surface == "endpoints"
                    else "not_applicable"
                ),
                "minimum_sessions": (
                    MIN_BASELINE_SESSIONS if scenario.surface == "endpoints" else None
                ),
                "prior_sessions": len(scenario.prior_scales),
            },
            "reason_z_threshold": REASON_Z_THRESHOLD,
            "score_top_k": SCORE_TOP_K,
        },
        "ranking_complete": True,
        "findings": findings,
        "error": None,
        "skip_reason": None,
    }
    legacy = {
        "scenario": scenario,
        "rank": target["rank"] - 1,
        "total": len(findings),
        "score": target["score"]["value"],
        "reasons": [reason["feature"] for reason in target["reasons"]],
        "passed": passed,
    }
    return ranking, legacy


def _skipped_ranking(
    scenario: Any,
    scenario_record: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    score_field = "anomaly_score" if scenario.surface == "endpoints" else "outbound_score"
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "ranking",
        "scenario_id": scenario.name,
        "scenario_version": SCHEMA_VERSION,
        "dataset_id": scenario_record["dataset_id"],
        "evaluation_surface": scenario.surface,
        "analytical_mode": {
            "name": "numeric-only",
            "state": "not_used",
        },
        "execution_status": "skipped",
        "duration_ms": 0.0,
        "quality_outcome": "not_evaluated",
        "expected_behavior": scenario.expect,
        "target": {
            "entity": scenario_record["target"],
            "rank": None,
            "score": None,
        },
        "evidence": {
            "capture_id": scenario_record["observation_window"]["capture_id"],
            "packet_rows_sha256": None,
            "packet_count": None,
        },
        "score_semantics": {
            "field": score_field,
            "order": "descending",
            "tie_behavior": "preserve-detector-emission-order",
        },
        "detector_parameters": {
            "cluster_labels": {
                "state": "not_applicable",
                "value": None,
            },
            "historical_baseline": {
                "state": ("not_used" if scenario.surface == "endpoints" else "not_applicable"),
                "minimum_sessions": (
                    MIN_BASELINE_SESSIONS if scenario.surface == "endpoints" else None
                ),
                "prior_sessions": len(scenario.prior_scales),
            },
            "reason_z_threshold": REASON_Z_THRESHOLD,
            "score_top_k": SCORE_TOP_K,
        },
        "ranking_complete": False,
        "findings": [],
        "error": None,
        "skip_reason": reason,
    }


def _package_inventory() -> list[dict[str, str]]:
    versions: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            versions[name.lower()] = distribution.version
    return [{"name": name, "version": versions[name]} for name in sorted(versions)]


def _tool_version(command: str) -> str | None:
    executable = shutil.which(command)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    first_line = (result.stdout or result.stderr).splitlines()
    return first_line[0].strip() if first_line else None


def _dockerfile_record(relative: str, subject_revision: str) -> dict[str, Any]:
    source = _git_file(subject_revision, relative)
    bases = []
    for line in source.decode("utf-8").splitlines():
        match = re.match(r"^\s*FROM\s+([^\s]+)", line, flags=re.IGNORECASE)
        if match:
            bases.append(match.group(1))
    return {
        "definition": relative,
        "state": "not_used",
        "sha256": sha256_bytes(source),
        "base_images": bases,
    }


def _environment_record(subject_revision: str) -> dict[str, Any]:
    tshark_version = _tool_version("tshark")
    gpu_version = _tool_version("nvidia-smi")
    executable = Path(sys.executable).resolve()
    try:
        executable_label = executable.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        executable_label = executable.name
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "environment",
        "captured_at": utc_now(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": executable_label,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "hardware": {
            "logical_cpu_count": os.cpu_count(),
            "memory_bytes": int(psutil.virtual_memory().total),
            "gpu": {
                "state": "not_used" if gpu_version else "unavailable",
                "devices": [gpu_version] if gpu_version else [],
            },
        },
        "packages": {
            "capture_method": "importlib.metadata",
            "items": _package_inventory(),
        },
        "tools": [
            {
                "name": "tshark",
                "state": "not_used" if tshark_version else "unavailable",
                "version": tshark_version,
            }
        ],
        "services": [
            {"name": "neo4j", "state": "not_used", "version": None},
            {"name": "openai-api", "state": "not_used", "version": None},
            {"name": "live-capture-interface", "state": "not_used", "version": None},
        ],
        "models": [
            {
                "role": "endpoint-embedding",
                "state": "not_used",
                "provider": None,
                "model": None,
                "revision": None,
                "digest": None,
            }
        ],
        "containers": [
            _dockerfile_record("harbor/Dockerfile", subject_revision),
            _dockerfile_record("ocean/Dockerfile", subject_revision),
        ],
        "environment_variables": [
            {
                "name": name,
                "state": ("set-with-value-redacted" if os.environ.get(name) else "unset"),
            }
            for name in SAFE_ENVIRONMENT_NAMES
        ],
    }


def _git_subject_file(revision: str, relative: str) -> dict[str, str]:
    subject = _git_file(revision, relative)
    current = (REPO_ROOT / relative).read_bytes()
    if current != subject:
        raise ContractViolation(
            f"{relative} differs from subject revision {revision}; "
            "Benchmark 0 collection would measure changed detector code"
        )
    return {
        "path": relative,
        "sha256": sha256_bytes(subject),
    }


def _artifact_inventory() -> list[dict[str, Any]]:
    records = [
        ("manifest.json", "manifest", "application/json"),
        ("datasets.json", "dataset_catalog", "application/json"),
        ("scenarios.json", "scenario_catalog", "application/json"),
        ("run.json", "run", "application/json"),
        ("environment.json", "environment", "application/json"),
        ("rankings.jsonl", "rankings", "application/x-ndjson"),
        ("evaluation.json", "evaluation", "application/json"),
        ("known-failures.json", "known_failures", "application/json"),
        ("logs/collector.stdout.log", "stdout", "text/plain"),
        ("logs/collector.stderr.log", "stderr", "text/plain"),
        ("report.md", "report", "text/markdown"),
        ("checksums.sha256", "checksums", "text/plain"),
    ]
    records.extend(
        (f"schemas/{filename}", "schema", "application/schema+json")
        for filename in SCHEMA_FILES.values()
    )
    return [
        {"path": path, "role": role, "media_type": media_type, "required": True}
        for path, role, media_type in records
    ]


def _manifest(
    created_at: str,
    subject_revision: str,
    collector_revision: str,
    collector_dirty: bool,
    canonical: bool,
) -> dict[str, Any]:
    command = "collect-baseline" if canonical else "collect-example"
    title = "JAWS Benchmark 0" if canonical else "JAWS Benchmark 0 contract example"
    description = (
        "Canonical observational freeze of JAWS detector behavior, complete rankings, "
        "known quality failures, unavailable evidence, and execution provenance."
        if canonical
        else "Noncanonical validation fixture proving that the Benchmark 0 contract can "
        "retain complete current rankings and explicit unavailable-data states."
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "manifest",
        "benchmark": {
            "benchmark_id": BENCHMARK_ID,
            "title": title,
            "canonical": canonical,
            "created_at": created_at,
            "description": description,
        },
        "subject": {
            "repository": "https://github.com/derekburgess/jaws",
            "revision": subject_revision,
            "detector_files": [
                _git_subject_file(subject_revision, "jaws/jaws_compute.py"),
                _git_subject_file(subject_revision, "jaws/jaws_finder.py"),
            ],
        },
        "collector": {
            "revision": collector_revision,
            "working_tree_dirty": collector_dirty,
            "source_sha256": (
                _tree_digest_at_revision(collector_revision, _collector_source_paths())
                if canonical
                else _tree_digest(_collector_source_paths())
            ),
        },
        "commands": [
            {
                "command_id": command,
                "argv": [
                    "python",
                    "-m",
                    "harness.benchmark_contract",
                    command,
                    "--subject-revision",
                    subject_revision,
                    "--collector-revision",
                    collector_revision,
                ],
                "cwd": ".",
                "environment": [
                    {
                        "name": name,
                        "state": ("set-with-value-redacted" if os.environ.get(name) else "unset"),
                    }
                    for name in SAFE_ENVIRONMENT_NAMES
                ],
            }
        ],
        "schema_catalog": [
            {
                "artifact_kind": kind,
                "path": f"schemas/{filename}",
                "schema_version": SCHEMA_VERSION,
            }
            for kind, filename in SCHEMA_FILES.items()
        ],
        "artifacts": _artifact_inventory(),
        "conventions": {
            "rank_base": 1,
            "emitted_position_base": 0,
            "score_order": "descending",
            "timestamp_format": "RFC3339-UTC",
            "null_policy": (
                "Null means a field is inapplicable or was not observed; absence is not "
                "silently treated as zero, false, success, or availability."
            ),
            "secret_policy": (
                "Environment-variable names and redacted presence may be recorded; "
                "credential values are prohibited."
            ),
            "checksum_algorithm": "sha256",
        },
    }


def _datasets(
    synthetic_records: list[dict[str, Any]],
    pcap_is_available: bool,
) -> dict[str, Any]:
    synthetic_digest = sha256_bytes(
        canonical_json_bytes(
            [
                {
                    "scenario_id": row["scenario_id"],
                    "packet_rows_sha256": row["packet_rows_sha256"],
                }
                for row in synthetic_records
            ]
        )
    )
    pcap_digest = sha256_file(Path(pcap_dir()) / NJRAT.filename) if pcap_is_available else None
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "dataset_catalog",
        "datasets": [
            {
                "dataset_id": "jaws-synthetic-v0",
                "dataset_version": SCHEMA_VERSION,
                "kind": "synthetic-packet-rows",
                "availability": "available",
                "source": {
                    "name": "JAWS controlled scenario generators",
                    "uri": None,
                    "license": "GPL-2.0-only",
                    "redistribution": "allowed",
                },
                "evidence": {
                    "format": "packet-row-json",
                    "sha256": synthetic_digest,
                    "expected_filename": None,
                    "generator": {
                        "qualified_name": "harness.scenarios.SCENARIOS",
                        "source_sha256": _tree_digest(
                            [
                                Path(__file__).with_name("scenarios.py"),
                                Path(__file__).with_name("recall.py"),
                            ]
                        ),
                    },
                },
                "labels": {
                    "method": "generator-declared",
                    "provenance": (
                        "Each generator plants one declared endpoint with a detect or "
                        "reject expectation."
                    ),
                },
                "unavailable_reason": None,
            },
            {
                "dataset_id": "mta-2026-01-29-njrat",
                "dataset_version": SCHEMA_VERSION,
                "kind": "pcap",
                "availability": "available" if pcap_is_available else "unavailable",
                "source": {
                    "name": "Malware-Traffic-Analysis.net 2026-01-29 njRAT infection",
                    "uri": "https://www.malware-traffic-analysis.net/2026/01/29/index.html",
                    "license": None,
                    "redistribution": "restricted",
                },
                "evidence": {
                    "format": "pcap",
                    "sha256": pcap_digest,
                    "expected_filename": NJRAT.filename,
                    "generator": None,
                },
                "labels": {
                    "method": "published-ioc",
                    "provenance": (
                        "Scenario IP labels are transcribed from the sample's published "
                        "IOC documentation; they are not inferred by JAWS."
                    ),
                },
                "unavailable_reason": (
                    None
                    if pcap_is_available
                    else "dataset_unavailable: JAWS_PCAP_DIR does not contain the documented file"
                ),
            },
        ],
    }


def _run_record(
    started_at: str,
    ended_at: str,
    duration_ms: float,
    rankings: list[dict[str, Any]],
    subject_revision: str,
    collector_revision: str,
    canonical: bool,
) -> dict[str, Any]:
    command = "collect-baseline" if canonical else "collect-example"
    argv = [
        "python",
        "-m",
        "harness.benchmark_contract",
        command,
    ]
    if canonical:
        argv.extend(
            [
                "--subject-revision",
                subject_revision,
                "--collector-revision",
                collector_revision,
            ]
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "run",
        "run_id": ("baseline-0-canonical" if canonical else "baseline-0-contract-example"),
        "benchmark_id": BENCHMARK_ID,
        "lifecycle": {
            "status": "completed",
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": round(duration_ms, 3),
        },
        "invocation": {
            "argv": argv,
            "cwd": ".",
            "exit_code": 0,
            "stdout_artifact": "logs/collector.stdout.log",
            "stderr_artifact": "logs/collector.stderr.log",
        },
        "analytical_modes": [
            {
                "name": "numeric-only",
                "state": "used",
                "reason": "The current recall harness ranks deterministic numeric profiles.",
            },
            {
                "name": "text-only",
                "state": "unavailable",
                "reason": "No stored embeddings or embedding model are used by this fixture.",
            },
            {
                "name": "blended",
                "state": "unavailable",
                "reason": "No stored embeddings or PCA/DBSCAN ablation is used by this fixture.",
            },
        ],
        "dependencies": [
            {
                "name": "neo4j",
                "state": "not_used",
                "detail": "Synthetic profiles are built directly from packet rows.",
            },
            {
                "name": "openai-api",
                "state": "not_used",
                "detail": "No embedding provider is invoked.",
            },
            {
                "name": "pcap-fixture",
                "state": ("used" if available(NJRAT.filename) else "unavailable"),
                "detail": (
                    NJRAT.filename
                    if available(NJRAT.filename)
                    else "JAWS_PCAP_DIR sample is absent; scenarios are explicit skips"
                ),
            },
        ],
        "scenario_statuses": [
            {
                "scenario_id": row["scenario_id"],
                "execution_status": row["execution_status"],
                "quality_outcome": row["quality_outcome"],
                "duration_ms": row["duration_ms"],
                "error": row["error"],
                "skip_reason": row["skip_reason"],
            }
            for row in rankings
        ],
    }


def render_report(bundle: Path | str) -> str:
    """Render the human report solely from retained machine-readable records."""
    bundle = Path(bundle)
    manifest = _read_json(bundle / "manifest.json")
    evaluation = _read_json(bundle / "evaluation.json")
    known = _read_json(bundle / "known-failures.json")
    rankings = {row["scenario_id"]: row for row in _read_jsonl(bundle / "rankings.jsonl")}
    aggregate = evaluation["aggregate"]
    metrics = {row["name"]: row["value"] for row in aggregate["metrics"]}
    canonical = manifest["benchmark"]["canonical"]
    cutoff = evaluation["cutoff"]
    recall_metric = metrics[f"recall_at_{cutoff}"]
    burden_metric = metrics[f"benign_top_{cutoff}_burden"]
    tick = "`"
    lines = [
        "# JAWS Benchmark 0" if canonical else "# JAWS Benchmark 0 contract example",
        "",
        "> This is a canonical Benchmark 0 result."
        if canonical
        else "> This is a noncanonical contract-validation fixture, not Benchmark 0.",
        "",
        f"- Detector subject: {tick}{manifest['subject']['revision']}{tick}",
        f"- Collector: {tick}{manifest['collector']['revision']}{tick} "
        f"(dirty: {tick}"
        f"{str(manifest['collector']['working_tree_dirty']).lower()}{tick})",
        f"- Completed scenarios: {aggregate['completed_count']}/{aggregate['scenario_count']}",
        f"- Recall@{cutoff}: {recall_metric:.3f}",
        f"- Mean reciprocal rank: {metrics['mean_reciprocal_rank']:.3f}",
        f"- Benign top-{cutoff} burden: {burden_metric}",
        "",
        "## Scenario results",
        "",
        "| Scenario | Expect | Surface | Status | Target rank | Score | Quality |",
        "| --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in evaluation["per_scenario"]:
        ranking = rankings[row["scenario_id"]]
        target = ranking["target"]
        rank = f"{target['rank']}/{len(ranking['findings'])}" if target["rank"] is not None else "—"
        score = f"{target['score']['value']:.4f}" if target["score"] is not None else "—"
        lines.append(
            f"| {row['scenario_id']} | {row['expectation']} | "
            f"{row['evaluation_surface']} | {row['execution_status']} | "
            f"{rank} | {score} | {row['quality_outcome']} |"
        )
    lines += [
        "",
        "## Known failures reproduced",
        "",
    ]
    failures = {row["failure_id"]: row for row in known["failures"]}
    if aggregate["known_failures_reproduced"]:
        for failure_id in aggregate["known_failures_reproduced"]:
            failure = failures[failure_id]
            lines.append(
                f"- {tick}{failure_id}{tick}: {failure['title']} "
                f"({', '.join(failure['scenario_ids'])})"
            )
    else:
        lines.append("- None.")
    skipped = [row for row in rankings.values() if row["execution_status"] == "skipped"]
    lines += [
        "",
        "## Unavailable evidence",
        "",
    ]
    if skipped:
        for row in skipped:
            lines.append(f"- {tick}{row['scenario_id']}{tick}: {row['skip_reason']}")
    else:
        lines.append("- None.")
    lines += [
        "",
        "## Integrity",
        "",
        "Every retained file except the checksum inventory itself is covered by "
        f"{tick}checksums.sha256{tick}. The validator also checks schema versions, "
        "complete rank "
        "ordering, cross-record references, deterministic evaluation, generated report "
        "parity, and common credential patterns.",
        "",
    ]
    return "\n".join(lines)


def _collect_bundle(
    output: Path | str,
    subject_revision: str,
    collector_revision: str,
    canonical: bool,
) -> Path:
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ContractViolation(f"refusing to overwrite non-empty bundle directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "logs").mkdir()
    (output / "schemas").mkdir()
    for filename in SCHEMA_FILES.values():
        shutil.copy2(SCHEMA_SOURCE / filename, output / "schemas" / filename)

    start_clock = time.perf_counter()
    started_at = utc_now()
    scenario_records: list[dict[str, Any]] = []
    rankings: list[dict[str, Any]] = []
    legacy_results: list[dict[str, Any]] = []

    for scenario in SCENARIOS:
        packet_rows = background_packets(seed=DEFAULT_SEED) + scenario.packets()
        scenario_record = _scenario_catalog_record(
            scenario, "jaws-synthetic-v0", "synthetic", packet_rows
        )
        ranking, legacy = _completed_ranking(scenario, scenario_record, packet_rows)
        scenario_records.append(scenario_record)
        rankings.append(ranking)
        legacy_results.append(legacy)

    pcap_is_available = available(NJRAT.filename)
    for scenario in documented_pcap_scenarios():
        if pcap_is_available:
            packet_rows = background_packets(seed=DEFAULT_SEED) + scenario.packets()
            scenario_record = _scenario_catalog_record(
                scenario, "mta-2026-01-29-njrat", "pcap", packet_rows
            )
            ranking, legacy = _completed_ranking(scenario, scenario_record, packet_rows)
            legacy_results.append(legacy)
        else:
            scenario_record = _scenario_catalog_record(
                scenario, "mta-2026-01-29-njrat", "pcap", None
            )
            ranking = _skipped_ranking(
                scenario,
                scenario_record,
                f"dataset_unavailable: JAWS_PCAP_DIR does not contain {NJRAT.filename}",
            )
        scenario_records.append(scenario_record)
        rankings.append(ranking)

    ended_at = utc_now()
    duration_ms = (time.perf_counter() - start_clock) * 1000.0
    scenarios_document = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "scenario_catalog",
        "scenarios": scenario_records,
    }
    known_document = {
        "schema_version": SCHEMA_VERSION,
        "artifact_kind": "known_failures",
        "failures": list(KNOWN_FAILURES),
    }
    datasets_document = _datasets(
        [row for row in scenario_records if row["source_type"] == "synthetic"],
        pcap_is_available,
    )
    evaluation_document = build_evaluation(scenarios_document, rankings, known_document)
    run_document = _run_record(
        started_at,
        ended_at,
        duration_ms,
        rankings,
        subject_revision,
        collector_revision,
        canonical,
    )
    manifest_document = _manifest(
        started_at,
        subject_revision,
        collector_revision,
        collector_revision == "worktree",
        canonical,
    )

    _write_json(output / "manifest.json", manifest_document)
    _write_json(output / "datasets.json", datasets_document)
    _write_json(output / "scenarios.json", scenarios_document)
    _write_json(output / "run.json", run_document)
    _write_json(output / "environment.json", _environment_record(subject_revision))
    _write_jsonl(output / "rankings.jsonl", rankings)
    _write_json(output / "evaluation.json", evaluation_document)
    _write_json(output / "known-failures.json", known_document)
    (output / "logs" / "collector.stdout.log").write_text(
        report(legacy_results) + "\n", encoding="utf-8"
    )
    (output / "logs" / "collector.stderr.log").write_text("", encoding="utf-8")
    (output / "report.md").write_text(render_report(output), encoding="utf-8")
    write_checksums(output)
    validate_bundle(output)
    return output


def collect_example_bundle(
    output: Path | str = DEFAULT_EXAMPLE,
    subject_revision: str = DEFAULT_SUBJECT_REVISION,
    collector_revision: str = "worktree",
) -> Path:
    """Collect a noncanonical fixture that exercises the entire bundle contract."""
    return _collect_bundle(
        output,
        subject_revision,
        collector_revision,
        canonical=False,
    )


def collect_baseline_bundle(
    output: Path | str = DEFAULT_BASELINE,
    subject_revision: str = DEFAULT_SUBJECT_REVISION,
    collector_revision: str | None = None,
) -> Path:
    """Collect canonical Benchmark 0 from a committed, clean collector revision."""
    if not collector_revision or collector_revision == "worktree":
        raise ContractViolation(
            "canonical collection requires --collector-revision naming a commit"
        )
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ContractViolation(f"refusing to overwrite non-empty bundle directory: {output}")
    changes = _worktree_changes()
    if changes:
        preview = ", ".join(changes[:5])
        raise ContractViolation(
            f"canonical collection requires a clean working tree; found {preview}"
        )
    resolved_subject = _resolve_commit(subject_revision)
    resolved_collector = _resolve_commit(collector_revision)
    expected_subject = _resolve_commit(DEFAULT_SUBJECT_REVISION)
    if resolved_subject != expected_subject:
        raise ContractViolation(
            "canonical Benchmark 0 must measure subject revision "
            f"{expected_subject}, got {resolved_subject}"
        )
    committed_digest = _tree_digest_at_revision(resolved_collector, _collector_source_paths())
    working_digest = _tree_digest(_collector_source_paths())
    if working_digest != committed_digest:
        raise ContractViolation("collector sources differ from --collector-revision")
    return _collect_bundle(
        output,
        resolved_subject,
        resolved_collector,
        canonical=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect, validate, and render the JAWS Benchmark 0 contract."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser(
        "collect-example", help="collect the noncanonical contract fixture"
    )
    collect.add_argument("--output", type=Path, default=DEFAULT_EXAMPLE)
    collect.add_argument("--subject-revision", default=DEFAULT_SUBJECT_REVISION)
    collect.add_argument("--collector-revision", default="worktree")

    baseline = subparsers.add_parser("collect-baseline", help="collect canonical Benchmark 0")
    baseline.add_argument("--output", type=Path, default=DEFAULT_BASELINE)
    baseline.add_argument("--subject-revision", default=DEFAULT_SUBJECT_REVISION)
    baseline.add_argument("--collector-revision", required=True)

    validate = subparsers.add_parser("validate", help="validate an existing bundle")
    validate.add_argument("bundle", type=Path)

    render = subparsers.add_parser("render", help="render report Markdown to stdout")
    render.add_argument("bundle", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "collect-example":
            path = collect_example_bundle(
                args.output, args.subject_revision, args.collector_revision
            )
            print(f"collected and validated {path}")
        elif args.command == "collect-baseline":
            path = collect_baseline_bundle(
                args.output, args.subject_revision, args.collector_revision
            )
            print(f"collected and validated canonical {path}")
        elif args.command == "validate":
            validate_bundle(args.bundle)
            print(f"validated {args.bundle}")
        else:
            print(render_report(args.bundle), end="")
    except ContractViolation as exc:
        print(f"benchmark contract error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
