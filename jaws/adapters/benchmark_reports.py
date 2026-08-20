"""JSON, Markdown, and HTML benchmark reports from one retained domain result."""

from __future__ import annotations

from html import escape

from jaws.domain import BenchmarkReport, ScenarioStatus, canonical_json


class BenchmarkReportRenderer:
    renderer_id = "benchmark_report"
    version = "1"

    def json(self, report: BenchmarkReport) -> str:
        return canonical_json(report) + "\n"

    def markdown(self, report: BenchmarkReport) -> str:
        completed = sum(item.status is ScenarioStatus.COMPLETED for item in report.results)
        lines = [
            f"# JAWS {report.benchmark_id}",
            "",
            f"Completed scenario/ranker cells: {completed}/{len(report.results)}",
            (
                "Coverage: "
                f"completed={report.coverage.completed}, missing={report.coverage.missing}, "
                f"skipped={report.coverage.skipped}, failed={report.coverage.failed}, "
                f"abstained={report.coverage.abstained}"
            ),
            f"Required simple baselines: {', '.join(report.required_baselines)}",
            "",
            "## Scenario results",
            "",
            "| Dataset | Scenario | Ranker | Seed/window | Status | Recall@3 | Burden@3 | Ranking/message | Run |",
            "| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
        for result in report.results:
            metrics = result.reward.flattened() if result.reward is not None else {}
            lines.append(
                "| "
                + " | ".join(
                    (
                        result.dataset_id.value if result.dataset_id else "unknown",
                        result.scenario_id,
                        result.ranker_id,
                        f"{result.seed}/{result.window}",
                        result.status.value,
                        _number(metrics.get("recall_at_3")),
                        _number(metrics.get("benign_burden_at_3")),
                        (
                            ", ".join(item.value for item in result.ranking)
                            if result.ranking
                            else result.message or "—"
                        ),
                        result.run_id.value if result.run_id else "—",
                    )
                )
                + " |"
            )
        lines.extend(
            (
                "",
                "## Aggregate metrics",
                "",
                "| Metric | Value | Contributing runs |",
                "| --- | ---: | --- |",
            )
        )
        for metric in report.aggregates:
            lines.append(
                f"| {metric.name} | {metric.value:.6g} | "
                f"{', '.join(item.value for item in metric.contributing_run_ids)} |"
            )
        lines.extend(
            (
                "",
                "## Paired deltas",
                "",
                "| Control | Treatment | Metric | Mean | 95% interval | Pairs |",
                "| --- | --- | --- | ---: | ---: | ---: |",
            )
        )
        for delta in report.paired_deltas:
            lines.append(
                f"| {delta.control_id} | {delta.treatment_id} | {delta.metric} | "
                f"{delta.mean_delta:.6g} | [{delta.lower_95:.6g}, {delta.upper_95:.6g}] | "
                f"{delta.pairs} |"
            )
        lines.extend(
            (
                "",
                "## Environment",
                "",
                "```json",
                canonical_json(report.environment),
                "```",
                "",
                "## Full reward vectors",
                "",
            )
        )
        for result in report.results:
            if result.reward is None or result.run_id is None:
                continue
            lines.extend(
                (
                    f"### {result.run_id.value}",
                    "",
                    "```json",
                    canonical_json(result.reward),
                    "```",
                    "",
                )
            )
        lines.extend(
            (
                "",
                "## Artifact checksums",
                "",
                *(
                    f"- `{run_id}`: `{digest}`"
                    for run_id, digest in report.artifact_checksums.items()
                ),
                "",
            )
        )
        return "\n".join(lines)

    def html(self, report: BenchmarkReport) -> str:
        markdown = self.markdown(report)
        return (
            '<!doctype html><html><head><meta charset="utf-8"><title>'
            + escape(report.benchmark_id)
            + "</title></head><body><pre>"
            + escape(markdown)
            + "</pre></body></html>\n"
        )


def _number(value: float | None) -> str:
    return "—" if value is None else f"{value:.6g}"
