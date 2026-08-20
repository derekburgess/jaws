"""Versioned explanations derived only from retained score contributions."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import BehavioralRank, ComparisonFrame, EntityId, EvidencePointer

from .feature_registry import NumericFeatureRegistry
from .legacy_ranking import score_without_feature

EXPLANATION_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ExplanationReason:
    feature: str
    value: float
    unit: str
    direction: str
    standardized_deviation: float
    weighted_deviation: float
    comparison_frame: str
    baseline: float | None
    baseline_depth: int
    host_relative: str | None


@dataclass(frozen=True, slots=True)
class FindingExplanation:
    schema_version: str
    entity_id: EntityId
    rank: int
    score: float
    reasons: tuple[ExplanationReason, ...]
    evidence: tuple[EvidencePointer, ...]
    infrastructure_caveat: str | None


@dataclass(frozen=True, slots=True)
class ExplanationAblation:
    entity_id: EntityId
    feature: str
    original_score: float
    ablated_score: float
    score_delta: float
    original_rank: int
    ablated_rank: int


class ExplanationService:
    def __init__(self, registry: NumericFeatureRegistry, *, reason_threshold: float = 2.5) -> None:
        self.registry = registry
        self.reason_threshold = reason_threshold

    def explain(
        self,
        finding: BehavioralRank,
        *,
        is_local: bool,
        cloud_hosted: bool = False,
    ) -> FindingExplanation:
        reasons = []
        for contribution in finding.contributions:
            z = contribution.standardized_deviation
            if abs(z) < self.reason_threshold:
                continue
            reasons.append(
                ExplanationReason(
                    feature=contribution.feature,
                    value=contribution.raw_value,
                    unit=contribution.unit,
                    direction="high" if z > 0 else "low",
                    standardized_deviation=z,
                    weighted_deviation=contribution.weighted_deviation,
                    comparison_frame=(
                        "own history"
                        if contribution.comparison_frame is ComparisonFrame.OWN_HISTORY
                        else "peer endpoints"
                    ),
                    baseline=contribution.baseline,
                    baseline_depth=contribution.baseline_depth,
                    host_relative=self.registry.host_relative_gloss(
                        contribution.feature, is_local=is_local
                    ),
                )
            )
        reasons.sort(key=lambda row: (-abs(row.standardized_deviation), row.feature))
        caveat = None
        if cloud_hosted:
            caveat = (
                "The organization identifies a hosting or cloud provider; it describes "
                "infrastructure ownership, not the hosted service's reputation."
            )
        return FindingExplanation(
            schema_version=EXPLANATION_SCHEMA_VERSION,
            entity_id=finding.entity_id,
            rank=finding.rank,
            score=finding.score,
            reasons=tuple(reasons),
            evidence=finding.evidence,
            infrastructure_caveat=caveat,
        )

    @staticmethod
    def ablate(
        findings: tuple[BehavioralRank, ...], *, entity_id: EntityId, feature: str, top_k: int = 3
    ) -> ExplanationAblation:
        target = next(row for row in findings if row.entity_id == entity_id)
        scores = {
            row.entity_id: (
                score_without_feature(row, feature, top_k=top_k)
                if row.entity_id == entity_id
                else row.score
            )
            for row in findings
        }
        ordered = sorted(scores, key=lambda identity: (-scores[identity], identity.value))
        ablated_rank = ordered.index(entity_id) + 1
        ablated_score = scores[entity_id]
        return ExplanationAblation(
            entity_id=entity_id,
            feature=feature,
            original_score=target.score,
            ablated_score=ablated_score,
            score_delta=target.score - ablated_score,
            original_rank=target.rank,
            ablated_rank=ablated_rank,
        )
