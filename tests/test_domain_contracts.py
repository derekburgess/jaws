"""Typed-domain identity, immutability, time, unit, and ranking contracts."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from jaws.adapters import UuidCaptureIdGenerator
from jaws.domain import (
    CAPTURE_TRANSITIONS,
    RUN_TRANSITIONS,
    CanonicalDigest,
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EndpointProfile,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityDefinition,
    EntityId,
    EntityMetadata,
    EntityType,
    EvidencePointer,
    ExperimentSpec,
    FindingId,
    ObservationScope,
    ObservationScopeId,
    ObservationWindow,
    OutlierStatus,
    PacketRecord,
    ProfileIdentity,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
    RepresentationSpec,
    ResearcherAnnotation,
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


def test_uuid_capture_ids_are_opaque_collision_resistant_runtime_identity():
    values = iter(
        (
            UUID("12345678-1234-5678-1234-567812345678"),
            UUID("87654321-4321-8765-4321-876543218765"),
        )
    )
    generator = UuidCaptureIdGenerator(lambda: next(values))

    first = generator.new()
    second = generator.new()

    assert first == CaptureId("cap_12345678123456781234567812345678")
    assert second == CaptureId("cap_87654321432187654321876543218765")
    assert first != second


def test_capture_record_tracks_live_and_import_lifecycle_without_inferred_perspective():
    registered_at = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    live = CaptureRecord(
        capture_id=CaptureId("cap_live"),
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        state=CaptureState.REGISTERED,
        registered_at=registered_at,
        legacy_capture_id="20260810T153000Z",
        perspective=EntityId("ip:10.0.0.2"),
        tool_versions={"jaws": "2.0.0", "tshark": "4.4.0"},
    )
    running = live.transition(CaptureState.RUNNING, registered_at + timedelta(seconds=1))
    complete = running.transition(
        CaptureState.COMPLETE,
        registered_at + timedelta(seconds=11),
        packet_count=42,
    )
    assert complete.packet_count == 42
    assert complete.ended_at == registered_at + timedelta(seconds=11)
    assert list(complete.tool_versions) == ["jaws", "tshark"]

    imported = CaptureRecord(
        capture_id=CaptureId("cap_import"),
        source_kind=CaptureSourceKind.PCAP_FILE,
        source_name="fixture.pcap",
        state=CaptureState.REGISTERED,
        registered_at=registered_at,
        content_digest=CanonicalDigest("a" * 64),
    ).transition(CaptureState.IMPORTING, registered_at)
    assert imported.perspective is None
    with pytest.raises(ValueError, match="invalid lifecycle transition"):
        complete.transition(CaptureState.RUNNING, registered_at + timedelta(seconds=12))


def test_packet_evidence_normalizes_addresses_and_requires_valid_units():
    packet = PacketRecord(
        capture_id=CaptureId("capture-a"),
        observed_at=datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
        protocol=" TCP ",
        size_bytes=64,
        source_ip="2001:0db8::1",
        destination_ip="192.0.2.1",
        source_port=443,
    )

    assert packet.protocol == "TCP"
    assert packet.source_ip == "2001:db8::1"
    with pytest.raises(ValueError, match="IP address"):
        replace(packet, source_ip="not-an-address")
    with pytest.raises(ValueError, match="port"):
        replace(packet, destination_port=0)
    with pytest.raises(ValueError, match="size"):
        replace(packet, size_bytes=-1)


def test_enrichment_and_profile_records_separate_provider_and_researcher_evidence():
    observed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    entity_id = EntityId("ip:2001:db8::1")
    enrichment = EnrichmentRecord(
        entity_id=entity_id,
        ip_address="2001:0db8::1",
        status=EnrichmentStatus.SUCCEEDED,
        acquired_at=observed_at,
        provider_id="fixture",
        provider_revision="1",
        organization="Example",
    )
    annotation = ResearcherAnnotation(
        entity_id=entity_id,
        key="label",
        value="benign",
        author="researcher",
        recorded_at=observed_at,
        ground_truth=True,
    )
    identity = ProfileIdentity(
        entity_id=entity_id,
        scope_id=ObservationScopeId("scope-a"),
        representation_id="description",
        representation_version="1",
        model_id="model",
        model_revision="revision",
    )
    profile = EndpointProfile(
        identity=identity,
        legacy_scope="capture-a",
        computed_at=observed_at,
        address_classification="documentation",
        protocols=("TCP", "TCP"),
        out_ports=(443, 443),
        embedding=(0.0, 1.0),
    )

    assert enrichment.ip_address == "2001:db8::1"
    metadata = EntityMetadata(
        entity_id=entity_id,
        ip_address="2001:0db8::1",
        organization=" Example ",
    )
    assert metadata.organization == "Example"
    assert annotation.ground_truth
    assert profile.protocols == ("TCP",)
    assert profile.out_ports == (443,)
    assert profile.outlier is OutlierStatus.NOT_SCORED
    with pytest.raises(ValueError, match="unsuccessful"):
        replace(enrichment, status=EnrichmentStatus.NOT_FOUND)
    with pytest.raises(ValueError, match="successful enrichment requires"):
        replace(enrichment, organization="   ")


def test_observation_scope_and_profile_identity_are_explicit_and_stable():
    created_at = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    capture_id = CaptureId("cap_example")
    scope = ObservationScope.for_capture(
        capture_id,
        created_at,
        perspective=EntityId("ip:10.0.0.2"),
        filters=("tcp",),
    )
    assert scope.scope_id == ObservationScopeId("scope_cap_example")
    assert scope.capture_ids == (capture_id,)

    first = ProfileIdentity(
        entity_id=EntityId("ip:8.8.8.8"),
        scope_id=scope.scope_id,
        representation_id="numeric",
        representation_version="2",
    )
    second = ProfileIdentity(
        entity_id=EntityId("ip:8.8.8.8"),
        scope_id=scope.scope_id,
        representation_id="numeric",
        representation_version="2",
    )
    assert first.profile_key == second.profile_key
    assert first.profile_key.startswith("profile_")
    with pytest.raises(ValueError, match="provided together"):
        ProfileIdentity(
            entity_id=EntityId("ip:8.8.8.8"),
            scope_id=scope.scope_id,
            representation_id="embedding",
            representation_version="1",
            model_id="model-only",
        )


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
