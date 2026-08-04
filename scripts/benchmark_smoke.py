#!/usr/bin/env python3
"""Run the controlled synthetic benchmark and emit report-only CI artifacts.

Metric outcomes are observations: this command exits nonzero only when the harness
cannot execute or its result shape is invalid. A rank, recall, or false-positive change
is retained in JSON and Markdown for review but is never converted into a CI failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / "tests"
CUTOFF = 3
SCHEMA_VERSION = "1.0.0"
EXPECTED_SCENARIOS = frozenset(
    {
        "behavioral_change",
        "bulk_download",
        "burst_exfil",
        "jittered_beacon",
        "payload_beacon",
        "slow_exfil",
        "stable_heavy",
        "tcp_keepalive",
    }
)


class Scenario(Protocol):
    """The scenario fields consumed by the reporting adapter."""

    name: str
    planted_ip: str
    expect: str
    surface: str


class RawEvaluation(TypedDict):
    """Current output shape of ``harness.recall.evaluate``."""

    scenario: Scenario
    rank: int | None
    total: int
    score: float | None
    reasons: list[str]
    passed: bool


class ScenarioResult(TypedDict):
    scenario_id: str
    expectation: Literal["detect", "reject"]
    surface: str
    planted_ip: str
    rank: int | None
    total: int
    score: float | None
    reasons: list[str]
    quality_outcome: Literal["passed", "failed"]


class Summary(TypedDict):
    scenario_count: int
    detection_hits: int
    detection_scenarios: int
    recall_at_3: float
    benign_top_three: int
    benign_scenarios: int


class SmokeReport(TypedDict):
    schema_version: str
    policy: Literal["report_only"]
    cutoff: int
    summary: Summary
    scenarios: list[ScenarioResult]


ScenarioLoader = Callable[[str], list[Scenario]]
Evaluator = Callable[[Scenario], RawEvaluation]


def _load_harness() -> tuple[ScenarioLoader, Evaluator]:
    """Load the test-owned benchmark harness without packaging it into JAWS."""

    test_root = str(TEST_ROOT)
    if test_root not in sys.path:
        sys.path.insert(0, test_root)
    harness = import_module("harness")
    return (
        cast(ScenarioLoader, getattr(harness, "all_scenarios")),
        cast(Evaluator, getattr(harness, "evaluate")),
    )


def collect_report() -> SmokeReport:
    """Execute every controlled scenario and normalize its report-only metrics."""

    all_scenarios, evaluate = _load_harness()
    scenarios = all_scenarios("synthetic")
    scenario_ids = [scenario.name for scenario in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise RuntimeError("synthetic benchmark scenario IDs are not unique")
    if set(scenario_ids) != EXPECTED_SCENARIOS:
        raise RuntimeError(
            "synthetic benchmark scenario set changed; update the smoke schema explicitly"
        )

    rows: list[ScenarioResult] = []
    for scenario in scenarios:
        raw = evaluate(scenario)
        if raw["scenario"].name != scenario.name:
            raise RuntimeError(f"evaluation identity mismatch for {scenario.name}")

        rank_zero_based = raw["rank"]
        total = raw["total"]
        if total <= 0:
            raise RuntimeError(f"{scenario.name} emitted an empty ranking")
        if rank_zero_based is not None and not 0 <= rank_zero_based < total:
            raise RuntimeError(f"{scenario.name} emitted invalid rank {rank_zero_based}/{total}")
        if scenario.expect not in {"detect", "reject"}:
            raise RuntimeError(f"{scenario.name} has unsupported expectation {scenario.expect!r}")

        expectation: Literal["detect", "reject"] = (
            "detect" if scenario.expect == "detect" else "reject"
        )
        quality_outcome: Literal["passed", "failed"] = "passed" if raw["passed"] else "failed"
        score = raw["score"]
        rows.append(
            {
                "scenario_id": scenario.name,
                "expectation": expectation,
                "surface": scenario.surface,
                "planted_ip": scenario.planted_ip,
                "rank": None if rank_zero_based is None else rank_zero_based + 1,
                "total": total,
                "score": None if score is None else float(score),
                "reasons": list(raw["reasons"]),
                "quality_outcome": quality_outcome,
            }
        )

    detection_rows = [row for row in rows if row["expectation"] == "detect"]
    benign_rows = [row for row in rows if row["expectation"] == "reject"]
    detection_hits = sum(row["quality_outcome"] == "passed" for row in detection_rows)
    benign_top_three = sum(row["quality_outcome"] == "failed" for row in benign_rows)
    if not detection_rows:
        raise RuntimeError("synthetic benchmark contains no detection scenarios")

    return {
        "schema_version": SCHEMA_VERSION,
        "policy": "report_only",
        "cutoff": CUTOFF,
        "summary": {
            "scenario_count": len(rows),
            "detection_hits": detection_hits,
            "detection_scenarios": len(detection_rows),
            "recall_at_3": detection_hits / len(detection_rows),
            "benign_top_three": benign_top_three,
            "benign_scenarios": len(benign_rows),
        },
        "scenarios": rows,
    }


def render_markdown(report: SmokeReport) -> str:
    """Render the machine-readable report as a GitHub step summary."""

    summary = report["summary"]
    lines = [
        "# Synthetic benchmark smoke report",
        "",
        (
            "This job is **report-only**: metric changes are research observations and do "
            "not change the process exit status. Harness execution or artifact-shape errors "
            "still fail the job."
        ),
        "",
        f"- Recall@{report['cutoff']}: "
        f"{summary['detection_hits']}/{summary['detection_scenarios']} "
        f"({summary['recall_at_3']:.3f})",
        f"- Benign top-{report['cutoff']} results: "
        f"{summary['benign_top_three']}/{summary['benign_scenarios']}",
        "",
        "| Scenario | Expect | Surface | Rank | Score | Outcome | Reasons |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for row in report["scenarios"]:
        rank = "—" if row["rank"] is None else f"{row['rank']}/{row['total']}"
        score = "—" if row["score"] is None else f"{row['score']:.3f}"
        reasons = ", ".join(row["reasons"]) or "—"
        lines.append(
            f"| {row['scenario_id']} | {row['expectation']} | {row['surface']} | "
            f"{rank} | {score} | {row['quality_outcome']} | {reasons} |"
        )
    return "\n".join(lines) + "\n"


def write_artifacts(report: SmokeReport, output_dir: Path) -> tuple[Path, Path]:
    """Write deterministic JSON and Markdown artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "metrics.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark-smoke"),
        help="Directory for metrics.json and report.md (default: benchmark-smoke).",
    )
    args = parser.parse_args(argv)

    report = collect_report()
    _, markdown_path = write_artifacts(report, cast(Path, args.output_dir))
    markdown = markdown_path.read_text(encoding="utf-8")

    github_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_summary:
        with Path(github_summary).open("a", encoding="utf-8") as stream:
            stream.write(markdown)
    print(markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
