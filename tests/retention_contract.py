"""Shared retention plan/apply contract for fake and Neo4j repositories."""

from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EndpointProfile,
    EntityId,
    ObservationScopeId,
    PacketRecord,
    ProfileIdentity,
    RetentionPolicy,
)
from jaws.ports import RetentionConflictError
from jaws.services import RetentionService


def _profile(address: str, capture_id: CaptureId, computed_at: datetime) -> EndpointProfile:
    return EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId(f"ip:{address}"),
            scope_id=ObservationScopeId(f"scope_{capture_id.value}"),
            representation_id="retention-fixture",
            representation_version="1",
            model_id="fixture-model",
            model_revision="fixture-revision",
        ),
        legacy_scope=capture_id.value,
        computed_at=computed_at,
        address_classification="documentation",
        embedding=(0.1, 0.2),
    )


def assert_retention_service_contract(repositories):
    observed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    address = "192.0.2.10"
    capture_ids = tuple(
        CaptureId(f"cap_retention_fixture_{name}") for name in ("first", "second", "third")
    )
    for index, capture_id in enumerate(capture_ids):
        started = observed_at + timedelta(seconds=index)
        repositories.captures.add(
            CaptureRecord(
                capture_id=capture_id,
                source_kind=CaptureSourceKind.LIVE_INTERFACE,
                source_name="eth0",
                state=CaptureState.REGISTERED,
                registered_at=started,
            ).transition(CaptureState.RUNNING, started)
        )
        repositories.packets.append(
            capture_id,
            (
                PacketRecord(
                    capture_id=capture_id,
                    observed_at=started,
                    protocol="TCP",
                    size_bytes=64,
                    source_ip=address,
                    destination_ip="198.51.100.20",
                    source_port=50000 + index,
                    destination_port=443,
                ),
            ),
        )
        repositories.profiles.replace_scope(
            ObservationScopeId(f"scope_{capture_id.value}"),
            (_profile(address, capture_id, observed_at + timedelta(seconds=index)),),
        )

    service = RetentionService(repositories.profiles)
    policy = RetentionPolicy.legacy_profile_limit(2)
    dry_run = service.dry_run(policy)
    assert not dry_run.applied
    assert dry_run.deleted_profile_records == 0
    assert [summary.legacy_scope for summary in dry_run.plan.retained_profile_scopes] == [
        capture_ids[2].value,
        capture_ids[1].value,
    ]
    assert [summary.legacy_scope for summary in dry_run.plan.deleted_profile_scopes] == [
        capture_ids[0].value
    ]
    assert dry_run.plan.profile_records_to_delete == 1
    assert repositories.profiles.read_scope(ObservationScopeId(f"scope_{capture_ids[0].value}"))

    # A recompute after dry-run invalidates the exact plan and performs no deletion.
    repositories.profiles.replace_scope(
        ObservationScopeId(f"scope_{capture_ids[0].value}"),
        (_profile(address, capture_ids[0], observed_at + timedelta(seconds=10)),),
    )
    with pytest.raises(RetentionConflictError):
        service.apply(dry_run.plan)
    assert all(
        repositories.profiles.read_scope(ObservationScopeId(f"scope_{capture_id.value}"))
        for capture_id in capture_ids
    )

    current = service.plan(policy)
    result = service.apply(current)
    assert result.applied
    assert result.deleted_profile_records == 1
    assert (
        repositories.profiles.read_scope(ObservationScopeId(f"scope_{capture_ids[1].value}")) == ()
    )
    assert repositories.profiles.read_scope(ObservationScopeId(f"scope_{capture_ids[0].value}"))
    assert repositories.profiles.read_scope(ObservationScopeId(f"scope_{capture_ids[2].value}"))

    keep_all = service.dry_run(RetentionPolicy.legacy_profile_limit(0))
    assert not keep_all.plan.has_deletions
