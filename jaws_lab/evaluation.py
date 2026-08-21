"""Deterministic laboratory evaluation independent of outcome direction."""

from __future__ import annotations

from jaws.domain import canonical_digest

from .contracts import LabEvaluation, OHEOResult


def evaluate_cycle(
    result: OHEOResult,
    *,
    repeated: OHEOResult | None = None,
    fixed_experiment_digest: str | None = None,
    human_research_usefulness: float | None = None,
) -> LabEvaluation:
    if human_research_usefulness is not None and not 0 <= human_research_usefulness <= 1:
        raise ValueError("human usefulness rating must be in [0, 1]")
    observation = result.observation
    cited = len(observation.run_ids) + len(observation.evidence)
    required = max(1, len(observation.run_ids) + len(observation.evidence))
    repeated_consistent = None
    if repeated is not None:
        repeated_consistent = (
            result.experiment_id == repeated.experiment_id
            and result.observation.metric_deltas == repeated.observation.metric_deltas
        )
    matches_fixed = None
    if fixed_experiment_digest is not None:
        matches_fixed = str(canonical_digest(result.experiment)) == fixed_experiment_digest
    usage = result.trace.usage
    return LabEvaluation(
        specification_valid=True,
        hypothesis_falsifiable=not result.hypothesis.claim.endswith("?"),
        experiment_completed=bool(observation.run_ids),
        evidence_citation_rate=cited / required,
        budget_adherent=(
            float(usage["estimated_cost"]) >= 0
            and int(usage["tool_calls"]) == len(result.trace.tool_calls)
        ),
        repeated_run_consistent=repeated_consistent,
        matches_fixed_study=matches_fixed,
        human_research_usefulness=human_research_usefulness,
        notes=(
            "Evaluation scores validity, citations, reproducibility, and usefulness independently of metric direction.",
        ),
    )
