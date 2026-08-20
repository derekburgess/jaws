"""Milestone 4 service contracts and legacy behavior parity."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from jsonschema import Draft202012Validator

from jaws.domain import (
    CaptureId,
    ComparisonFrame,
    EndpointInspection,
    EndpointProfile,
    EntityId,
    ObservationScopeId,
    ProfileIdentity,
    RankerSpec,
    ReferenceEligibility,
    ReferenceKind,
    ReferenceSpec,
    primitive,
)
from jaws.jaws_finder import score_endpoints
from jaws.services import (
    ENDPOINT_FEATURE_REGISTRY_V1,
    HOST_DESTINATION_FEATURE_REGISTRY_V1,
    ComparisonRequest,
    ComparisonService,
    ExplanationService,
    InspectionRequest,
    InspectionService,
    KDistanceEpsilonStrategy,
    ReferenceObservation,
    TypedReferenceBuilder,
)


def _entities(rows):
    return tuple(EntityId(f"ip:{row['ip_address']}") for row in rows)


def _legacy_spec(**parameters):
    return RankerSpec(ranker_id="legacy_2_0", version="1", seed=0, parameters=parameters)


def test_peer_service_matches_legacy_endpoint_scores(pack):
    entities = _entities(pack)
    result = ComparisonService(ENDPOINT_FEATURE_REGISTRY_V1).compare(
        ComparisonRequest(
            entities=entities,
            rows=tuple(pack),
            histories={},
            reference=ReferenceSpec(kind=ReferenceKind.PEER),
            ranker=_legacy_spec(),
            capture_id="cap_scored",
        )
    )
    legacy = score_endpoints(pack, np.zeros(len(pack), dtype=int), None)
    expected = {row["ip_address"]: row["anomaly_score"] for row in legacy}
    assert [row.entity_id.value.removeprefix("ip:") for row in result.findings] == [
        row["ip_address"] for row in legacy
    ]
    for finding in result.findings:
        assert finding.score == pytest.approx(
            expected[finding.entity_id.value.removeprefix("ip:")], abs=5e-5
        )
        assert finding.evidence[0].selector is not None
        assert finding.evidence[0].capture_id == CaptureId("cap_scored")


def test_hybrid_reference_marks_history_states_and_keeps_timing_peer_relative(pack):
    returning = pack[0]
    first_seen = pack[1]
    thin = pack[2]
    observations = (
        ReferenceObservation(
            _entities([returning])[0], returning, (returning, returning), "cap_current"
        ),
        ReferenceObservation(_entities([first_seen])[0], first_seen, (), "cap_current"),
        ReferenceObservation(_entities([thin])[0], thin, (thin,), "cap_current"),
    )
    result = TypedReferenceBuilder(ENDPOINT_FEATURE_REGISTRY_V1).build(
        observations,
        ReferenceSpec(
            kind=ReferenceKind.HYBRID,
            parameters={"min_sessions": 2, "peer_features": ("interval_mean", "interval_cv")},
        ),
    )
    assert [row.eligibility for row in result.entities] == [
        ReferenceEligibility.HISTORICAL,
        ReferenceEligibility.FIRST_SEEN,
        ReferenceEligibility.INSUFFICIENT_HISTORY,
    ]
    timing = [result.feature_names.index(name) for name in ("interval_mean", "interval_cv")]
    assert all(result.frames[0][index] is ComparisonFrame.PEER for index in timing)
    assert result.frames[0][0] is ComparisonFrame.OWN_HISTORY


def test_pooled_history_is_rejected_and_researcher_filters_are_enforced(pack):
    observations = tuple(
        ReferenceObservation(entity, row, (), "all" if index == 0 else "cap_b", ("lab",))
        for index, (entity, row) in enumerate(zip(_entities(pack[:2]), pack[:2], strict=True))
    )
    builder = TypedReferenceBuilder(ENDPOINT_FEATURE_REGISTRY_V1)
    with pytest.raises(ValueError, match="pooled scope"):
        builder.build(observations, ReferenceSpec(kind=ReferenceKind.HYBRID))
    filtered = builder.build(
        tuple(replace(row, capture_id="cap_a" if index == 0 else "cap_b") for index, row in enumerate(observations)),
        ReferenceSpec(
            kind=ReferenceKind.RESEARCHER_DEFINED,
            parameters={"capture_ids": ("cap_b",)},
        ),
    )
    assert filtered.population == (_entities(pack[:2])[1],)
    assert filtered.excluded == (_entities(pack[:2])[0],)


def test_registry_rejects_nonfinite_and_exposes_host_flow_metadata(pack):
    broken = dict(pack[0], bytes_out=float("inf"))
    with pytest.raises(ValueError, match="finite"):
        ENDPOINT_FEATURE_REGISTRY_V1.raw_matrix((broken,))
    bytes_out = next(row for row in ENDPOINT_FEATURE_REGISTRY_V1.metadata if row.name == "bytes_out")
    assert bytes_out.required_evidence == ("bytes_out",)
    assert bytes_out.host_flow is not None
    assert "downloaded" in bytes_out.host_flow.remote_interpretation


def test_host_destination_uses_same_service_and_declared_legacy_active_features():
    rows = (
        {"ip_address": "8.8.8.8", "upload_bytes": 100, "upload_packets": 2, "download_bytes": 50, "download_packets": 1},
        {"ip_address": "1.1.1.1", "upload_bytes": 110, "upload_packets": 2, "download_bytes": 60, "download_packets": 1},
        {"ip_address": "9.9.9.9", "upload_bytes": 100000, "upload_packets": 200, "download_bytes": 1, "download_packets": 1},
    )
    entities = _entities(rows)
    result = ComparisonService(HOST_DESTINATION_FEATURE_REGISTRY_V1).compare(
        ComparisonRequest(
            entities=entities,
            rows=rows,
            histories={},
            reference=ReferenceSpec(kind=ReferenceKind.PEER),
            ranker=_legacy_spec(
                active_features=("upload_bytes", "upload_packets", "upload_download_ratio")
            ),
        )
    )
    assert result.findings[0].entity_id == EntityId("ip:9.9.9.9")
    assert result.representation.feature_names == (
        "upload_bytes",
        "upload_packets",
        "download_bytes",
        "download_packets",
        "upload_download_ratio",
    )


def test_labels_are_separate_deterministic_artifacts_and_record_pca(pack):
    rows = tuple(pack[:8])
    entities = _entities(rows)
    embeddings = tuple((float(i), float(i % 2), float(i % 3)) for i in range(len(rows)))
    request = ComparisonRequest(
        entities=entities,
        rows=rows,
        histories={},
        reference=ReferenceSpec(kind=ReferenceKind.PEER),
        ranker=_legacy_spec(),
        embeddings=embeddings,
        components=2,
        min_samples=4,
        eps=1.5,
    )
    service = ComparisonService(ENDPOINT_FEATURE_REGISTRY_V1)
    first = service.compare(request)
    second = service.compare(request)
    assert first.clusters == second.clusters
    assert first.representation.explained_variance
    assert first.representation.pca_components == 2
    assert tuple(row.model_label for row in first.findings) != ()
    assert len(first.findings) == len(rows)
    assert any(row.score > 0 for row in first.findings)


def test_epsilon_strategy_declares_override_and_median_or_knee():
    matrix = np.array([[0.0], [0.1], [0.2], [4.0]])
    strategy = KDistanceEpsilonStrategy()
    automatic = strategy.recommend(matrix, 2)
    override = strategy.recommend(matrix, 2, override=0.75)
    assert automatic.source in {"knee", "median"}
    assert override.value == 0.75
    assert override.source == "override"


def test_explanations_use_retained_contributions_and_support_fidelity_ablation(pack):
    rows = tuple(pack + [dict(pack[0], ip_address="10.0.0.250", bytes_out=1_000_000)])
    result = ComparisonService(ENDPOINT_FEATURE_REGISTRY_V1).compare(
        ComparisonRequest(
            entities=_entities(rows),
            rows=rows,
            histories={},
            reference=ReferenceSpec(kind=ReferenceKind.PEER),
            ranker=_legacy_spec(),
        )
    )
    top = result.findings[0]
    service = ExplanationService(ENDPOINT_FEATURE_REGISTRY_V1)
    explanation = service.explain(top, is_local=False, cloud_hosted=True)
    local = service.explain(top, is_local=True)
    snapshot = primitive(explanation)
    assert snapshot["schema_version"] == "1.0.0"
    assert set(snapshot) == {
        "schema_version",
        "entity_id",
        "rank",
        "score",
        "reasons",
        "evidence",
        "infrastructure_caveat",
    }
    schema = json.loads(
        (Path(__file__).parents[1] / "docs/schemas/explanation-1.0.0.schema.json").read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(snapshot)
    assert explanation.reasons
    directional = next(
        reason for reason in explanation.reasons if reason.host_relative is not None
    )
    local_directional = next(
        reason for reason in local.reasons if reason.feature == directional.feature
    )
    assert directional.host_relative != local_directional.host_relative
    assert explanation.infrastructure_caveat and "not" in explanation.infrastructure_caveat
    effect = service.ablate(result.findings, entity_id=top.entity_id, feature="bytes_out")
    assert effect.ablated_score <= effect.original_score


class _InspectionRepository:
    def __init__(self, detail):
        self.detail = detail

    def inspect(self, entity_id, *, peer_limit, packet_limit, history_limit):
        return self.detail

    def recent_profiles(self, *, computed_after, limit):
        return ()

    def profile(self, entity_id, capture_id):
        return next(
            (row for row in self.detail.history if row.legacy_scope == capture_id.value), None
        )


def _profile(capture):
    return EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId("ip:198.51.100.2"),
            scope_id=ObservationScopeId(f"scope_{capture}"),
            representation_id="fixture",
            representation_version="1",
            model_id="fixture",
            model_revision="1",
        ),
        legacy_scope=capture,
        computed_at=datetime(2026, 8, 20, tzinfo=UTC),
        address_classification="public",
        bytes_out=10,
        packets_out=1,
        out_peers=1,
        bytes_in=20,
        packets_in=1,
        in_peers=1,
    )


def test_inspection_service_selects_requested_profile_and_retains_all_session_context():
    latest = _profile("cap_latest")
    earlier = _profile("cap_earlier")
    detail = EndpointInspection(
        entity_id=latest.identity.entity_id,
        profile=latest,
        total_packets=42,
        total_peers=7,
        history=(latest, earlier),
    )
    scoped = InspectionService(_InspectionRepository(detail)).inspect(
        InspectionRequest(latest.identity.entity_id, CaptureId("cap_earlier"))
    )
    assert scoped.inspection.profile == earlier
    assert scoped.inspection.total_packets == 42
    assert scoped.all_session_totals is True
    assert scoped.evidence.capture_id == CaptureId("cap_earlier")
    assert "heuristic" in scoped.port_heuristic_note
