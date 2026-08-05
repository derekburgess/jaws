"""Typed-domain identity, immutability, time, unit, and ranking contracts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from jaws.domain import (
    CAPTURE_TRANSITIONS,
    RUN_TRANSITIONS,
    CaptureId,
    CaptureState,
    EntityDefinition,
    EntityId,
    EntityType,
    EvidencePointer,
    ExperimentSpec,
    FindingId,
    ObservationWindow,
    OutlierStatus,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
    RepresentationSpec,
    RunState,
    Score,
    ScoredEntity,
    ScoreDirection,
    canonical_json,
    deterministic_ranking,
    interval_seconds,
    normalize_utc,
    packet_count,
    ratio,
    require_transition,
    utc_text,
)


def _experiment(parameters=None):
    window = ObservationWindow(
        capture_ids=(CaptureId("capture-a"),),
        started_at=datetime(2026, 8, 5, 12, tzinfo=UTC),
    )
    return ExperimentSpec(
        hypothesis="regular endpoints rank above background",
        observation=window,
        entity=EntityDefinition(entity_type=EntityType.ENDPOINT_IP),
        representation=RepresentationSpec(
            representation_id="numeric", parameters=parameters or {"features": ["bytes"]}
        ),
        reference=ReferenceSpec(),
        ranker=RankerSpec(ranker_id="baseline", seed=7),
    )


def test_identifier_classes_are_runtime_distinct_and_validated():
    capture = CaptureId("same")
    entity = EntityId("same")
    assert capture != entity
    assert type(capture) is CaptureId
    with pytest.raises(ValueError):
        CaptureId("  ")


def test_specs_are_frozen_and_digest_semantic_content():
    first = _experiment({"b": 2, "a": 1})
    second = _experiment({"a": 1, "b": 2})
    assert first.digest == second.digest
    assert first.experiment_id.value == str(first.digest)
    with pytest.raises(FrozenInstanceError):
        first.hypothesis = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.representation.parameters["a"] = 2  # type: ignore[index,union-attr]


def test_timestamps_normalize_to_canonical_utc():
    eastern = datetime(2026, 8, 5, 8, tzinfo=timezone(-timedelta(hours=4)))
    assert normalize_utc(eastern) == datetime(2026, 8, 5, 12, tzinfo=UTC)
    assert utc_text(eastern) == "2026-08-05T12:00:00.000000Z"
    with pytest.raises(ValueError):
        normalize_utc(datetime(2026, 8, 5, 12))


def test_units_reject_invalid_values():
    assert packet_count(3).unit.value == "packets"
    assert interval_seconds(0.25).value == 0.25
    assert ratio(1.0).value == 1.0
    with pytest.raises(ValueError):
        packet_count(-1)
    with pytest.raises(ValueError):
        ratio(1.01)


def test_lifecycle_transitions_are_explicit():
    assert (
        require_transition(CaptureState.RUNNING, CaptureState.COMPLETE, CAPTURE_TRANSITIONS)
        is CaptureState.COMPLETE
    )
    with pytest.raises(ValueError):
        require_transition(CaptureState.COMPLETE, CaptureState.RUNNING, CAPTURE_TRANSITIONS)


def test_equal_scores_have_input_order_independent_ties():
    rows = [
        ScoredEntity(EntityId("z"), 1.0),
        ScoredEntity(EntityId("a"), 1.0),
        ScoredEntity(EntityId("m"), 2.0),
    ]
    ranked = deterministic_ranking(rows, ScoreDirection.HIGHER_IS_MORE_ANOMALOUS)
    assert [row.entity_id.value for row in ranked] == ["m", "a", "z"]
    assert deterministic_ranking(reversed(rows), ScoreDirection.HIGHER_IS_MORE_ANOMALOUS) == ranked


def test_score_and_outlier_are_independent_finding_fields():
    evidence = EvidencePointer(capture_id=CaptureId("capture-a"), entity_id=EntityId("ip:a"))
    finding = RankedFinding(
        finding_id=FindingId("finding-a"),
        entity_id=EntityId("ip:a"),
        rank=1,
        score=Score(0.99, ScoreDirection.HIGHER_IS_MORE_ANOMALOUS),
        outlier=OutlierStatus.INLIER,
        evidence=(evidence,),
    )
    assert finding.score.value == 0.99
    assert finding.outlier is OutlierStatus.INLIER


def test_canonical_json_is_compact_unicode_and_rejects_nonfinite_numbers():
    assert canonical_json({"z": "café", "a": 1}) == '{"a":1,"z":"café"}'
    with pytest.raises(ValueError):
        canonical_json({"score": float("nan")})


def test_evidence_requires_a_durable_join_identity():
    with pytest.raises(ValueError):
        EvidencePointer(entity_id=EntityId("ip:a"))


def test_run_lifecycle_is_terminal_after_success():
    assert (
        require_transition(RunState.CREATED, RunState.RUNNING, RUN_TRANSITIONS) is RunState.RUNNING
    )
    with pytest.raises(ValueError):
        require_transition(RunState.SUCCEEDED, RunState.RUNNING, RUN_TRANSITIONS)


def test_lower_score_direction_uses_the_same_stable_tie_break():
    rows = [
        ScoredEntity(EntityId("z"), 1.0),
        ScoredEntity(EntityId("a"), 1.0),
        ScoredEntity(EntityId("m"), 0.5),
    ]
    ranked = deterministic_ranking(rows, ScoreDirection.LOWER_IS_MORE_ANOMALOUS)
    assert [row.entity_id.value for row in ranked] == ["m", "a", "z"]
