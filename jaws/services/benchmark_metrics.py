"""Deterministic benchmark reward vectors, stability, and paired comparisons."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from math import log2, sqrt
from statistics import mean, stdev

from jaws.domain import (
    AggregateMetric,
    CoverageMetrics,
    EntityId,
    LabelRecord,
    MetricAtK,
    PairedDelta,
    RankedFinding,
    ResourceUsage,
    RewardVector,
    RunId,
    ScenarioBenchmarkResult,
    ScenarioStatus,
    StabilityMetrics,
    TargetRank,
)


def evaluate_reward_vector(
    findings: Sequence[RankedFinding],
    labels: Sequence[LabelRecord],
    *,
    cutoffs: tuple[int, ...] = (1, 3, 5, 10),
    usage: ResourceUsage | None = None,
    stability: StabilityMetrics | None = None,
    false_positive_movement: Mapping[str, float] | None = None,
    explanation_fidelity: float | None = None,
    scalar_weights: Mapping[str, float] | None = None,
) -> RewardVector:
    ordered = tuple(sorted(findings, key=lambda item: item.rank))
    if tuple(item.rank for item in ordered) != tuple(range(1, len(ordered) + 1)):
        raise ValueError("benchmark findings must be a complete contiguous ranking")
    identities = tuple(item.entity_id for item in ordered)
    if any(entity is None for entity in identities) or len(set(identities)) != len(identities):
        raise ValueError("benchmark findings require unique entity identities")
    relevance = {label.entity_id: label.relevance for label in labels}
    relevant = {entity for entity, grade in relevance.items() if grade > 0}
    ranked = tuple(entity for entity in identities if entity is not None)
    positions = {entity: rank for rank, entity in enumerate(ranked, 1)}
    recall = (
        tuple(
            MetricAtK(k, sum(entity in relevant for entity in ranked[:k]) / len(relevant))
            for k in cutoffs
        )
        if relevant
        else ()
    )
    relevant_positions = tuple(positions[entity] for entity in relevant if entity in positions)
    reciprocal_rank = 1.0 / min(relevant_positions) if relevant_positions else 0.0
    ndcg = tuple(MetricAtK(k, _ndcg(ranked, relevance, k)) for k in cutoffs)
    benign = tuple(
        MetricAtK(
            k,
            float(sum(relevance.get(entity, 0) == 0 for entity in ranked[:k])),
        )
        for k in cutoffs
    )
    first_relevant = min(relevant_positions) if relevant_positions else len(ranked) + 1
    benign_above = sum(
        relevance.get(entity, 0) == 0 for entity in ranked[: max(first_relevant - 1, 0)]
    )
    denominator = max(len(ranked) - 1, 1)
    targets = tuple(
        TargetRank(entity, positions[entity], (len(ranked) - positions[entity]) / denominator)
        for entity in sorted(relevant & set(positions), key=lambda item: item.value)
    )
    base = RewardVector(
        recall=recall,
        mean_reciprocal_rank=reciprocal_rank,
        ndcg=ndcg,
        benign_burden=benign,
        benign_above_first_relevant=benign_above,
        target_ranks=targets,
        stability=stability,
        false_positive_movement=false_positive_movement or {},
        explanation_fidelity=explanation_fidelity,
        usage=usage or ResourceUsage(),
        coverage=CoverageMetrics(1, 1),
    )
    weights = scalar_weights or {}
    if not weights:
        return base
    flattened = base.flattened()
    missing = set(weights) - set(flattened)
    if missing:
        raise ValueError(f"scalar weights reference unavailable metrics: {sorted(missing)}")
    objective = sum(float(weight) * flattened[name] for name, weight in weights.items())
    return RewardVector(
        recall=base.recall,
        mean_reciprocal_rank=base.mean_reciprocal_rank,
        ndcg=base.ndcg,
        benign_burden=base.benign_burden,
        benign_above_first_relevant=base.benign_above_first_relevant,
        target_ranks=base.target_ranks,
        stability=base.stability,
        false_positive_movement=base.false_positive_movement,
        explanation_fidelity=base.explanation_fidelity,
        usage=base.usage,
        coverage=base.coverage,
        scalar_objective=objective,
        scalar_weights=weights,
    )


def stability_metrics(
    left: Sequence[EntityId],
    right: Sequence[EntityId],
    *,
    k: int,
    parameter_distance: float = 0.0,
) -> StabilityMetrics:
    if not left or set(left) != set(right) or len(left) != len(set(left)):
        raise ValueError("stability requires complete rankings of the same unique entities")
    top_left = set(left[:k])
    top_right = set(right[:k])
    union = top_left | top_right
    overlap = len(top_left & top_right) / len(union) if union else 1.0
    left_rank = {entity: rank for rank, entity in enumerate(left, 1)}
    right_rank = {entity: rank for rank, entity in enumerate(right, 1)}
    count = len(left)
    squared = sum((left_rank[entity] - right_rank[entity]) ** 2 for entity in left)
    correlation = 1.0 if count < 2 else 1 - (6 * squared) / (count * (count**2 - 1))
    percentile_movement = mean(
        abs(left_rank[entity] - right_rank[entity]) / max(count - 1, 1) for entity in left
    )
    sensitivity = percentile_movement / parameter_distance if parameter_distance > 0 else 0.0
    return StabilityMetrics(overlap, correlation, sensitivity)


def false_positive_movement(
    control: Sequence[EntityId],
    treatment: Sequence[EntityId],
    labels: Sequence[LabelRecord],
    *,
    k: int,
) -> Mapping[str, float]:
    families = {label.entity_id: label.family for label in labels if label.relevance == 0}
    control_top = set(control[:k])
    treatment_top = set(treatment[:k])
    result: dict[str, float] = defaultdict(float)
    for entity, family in families.items():
        result[family] += float(entity in treatment_top) - float(entity in control_top)
    return dict(result)


def aggregate_metrics(
    results: Sequence[ScenarioBenchmarkResult],
) -> tuple[AggregateMetric, ...]:
    values: dict[tuple[str, str], list[tuple[float, RunId]]] = defaultdict(list)
    for result in results:
        if (
            result.status is not ScenarioStatus.COMPLETED
            or result.reward is None
            or result.run_id is None
        ):
            continue
        for metric, value in result.reward.flattened().items():
            values[(result.ranker_id, metric)].append((value, result.run_id))
    return tuple(
        AggregateMetric(
            f"{ranker_id}.{metric}",
            mean(value for value, _ in rows),
            tuple(run_id for _, run_id in rows),
        )
        for (ranker_id, metric), rows in sorted(values.items())
    )


def paired_deltas(
    control: Sequence[ScenarioBenchmarkResult],
    treatment: Sequence[ScenarioBenchmarkResult],
) -> tuple[PairedDelta, ...]:
    left = {
        _pair_key(item): item
        for item in control
        if item.status is ScenarioStatus.COMPLETED and item.reward is not None
    }
    right = {
        _pair_key(item): item
        for item in treatment
        if item.status is ScenarioStatus.COMPLETED and item.reward is not None
    }
    sample_ids = sorted(set(left) & set(right), key=str)
    by_metric: dict[str, list[float]] = defaultdict(list)
    for sample_id in sample_ids:
        left_metrics = left[sample_id].reward.flattened()  # type: ignore[union-attr]
        right_metrics = right[sample_id].reward.flattened()  # type: ignore[union-attr]
        for metric in set(left_metrics) & set(right_metrics):
            by_metric[metric].append(right_metrics[metric] - left_metrics[metric])
    rows: list[PairedDelta] = []
    control_id = control[0].ranker_id if control else "control"
    treatment_id = treatment[0].ranker_id if treatment else "treatment"
    for metric, deltas in sorted(by_metric.items()):
        average = mean(deltas)
        margin = 0.0 if len(deltas) < 2 else 1.96 * stdev(deltas) / sqrt(len(deltas))
        rows.append(
            PairedDelta(
                control_id,
                treatment_id,
                metric,
                average,
                average - margin,
                average + margin,
                len(deltas),
            )
        )
    return tuple(rows)


def _pair_key(
    result: ScenarioBenchmarkResult,
) -> tuple[str, str, str, str, int, str, str]:
    return (
        result.dataset_id.value if result.dataset_id is not None else "unknown",
        result.scenario_id,
        result.representation_id,
        result.reference_kind.value,
        result.seed,
        result.window,
        str(result.parameter_digest),
    )


def coverage_for(results: Sequence[ScenarioBenchmarkResult]) -> CoverageMetrics:
    counts = {status: 0 for status in ScenarioStatus}
    for result in results:
        counts[result.status] += 1
    return CoverageMetrics(
        len(results),
        counts[ScenarioStatus.COMPLETED],
        counts[ScenarioStatus.MISSING],
        counts[ScenarioStatus.SKIPPED],
        counts[ScenarioStatus.FAILED],
        counts[ScenarioStatus.ABSTAINED],
    )


def validate_comparative_claim(
    control: RewardVector, treatment: RewardVector, *, recall_k: int
) -> None:
    control_recall = next(item.value for item in control.recall if item.k == recall_k)
    treatment_recall = next(item.value for item in treatment.recall if item.k == recall_k)
    control_burden = next(item.value for item in control.benign_burden if item.k == recall_k)
    treatment_burden = next(item.value for item in treatment.benign_burden if item.k == recall_k)
    if treatment_recall > control_recall and (
        treatment_burden > control_burden
        or treatment.coverage.completed < control.coverage.completed
    ):
        raise ValueError(
            "recall-only improvement claim is prohibited when benign burden or coverage worsens"
        )


def _ndcg(ranking: Sequence[EntityId], relevance: Mapping[EntityId, int], k: int) -> float:
    observed = sum(
        (2 ** relevance.get(entity, 0) - 1) / log2(rank + 1)
        for rank, entity in enumerate(ranking[:k], 1)
    )
    ideal_grades = sorted(relevance.values(), reverse=True)[:k]
    ideal = sum((2**grade - 1) / log2(rank + 1) for rank, grade in enumerate(ideal_grades, 1))
    return observed / ideal if ideal else 0.0
