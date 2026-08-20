"""Shared fixtures and behavior for portable evidence export/import."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    ArchivedEntity,
    ArchivedObservationScope,
    ArchivedProfile,
    CanonicalDigest,
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureSourceMetadata,
    CaptureState,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    EvidenceSchemaProvenance,
    EvidenceSnapshot,
    ObservationScopeId,
    ObservationScopeKind,
    OutlierStatus,
    PacketRecord,
    ProfileStatus,
    ResearcherAnnotation,
    SchemaMigrationProvenance,
)
from jaws.ports import EvidenceImportConflictError, FrozenClock, InMemoryEvidenceRepository
from jaws.services import EvidenceTransferService
from jaws.storage.migrations import MIGRATIONS

OBSERVED_AT = datetime(2026, 8, 13, 12, tzinfo=UTC)


def schema_provenance() -> EvidenceSchemaProvenance:
    return EvidenceSchemaProvenance(
        version=MIGRATIONS[-1].version,
        migrations=tuple(
            SchemaMigrationProvenance(item.version, item.name, item.checksum) for item in MIGRATIONS
        ),
    )


def evidence_fixture() -> EvidenceSnapshot:
    schema = schema_provenance()
    capture_id = CaptureId("cap_evidence_fixture")
    scope_id = ObservationScopeId("scope_cap_evidence_fixture")
    source = EntityId("ip:192.0.2.10")
    destination = EntityId("ip:198.51.100.20")
    capture = CaptureRecord(
        capture_id=capture_id,
        source_kind=CaptureSourceKind.PCAP_FILE,
        source_name="evidence-fixture.pcap",
        state=CaptureState.COMPLETE,
        registered_at=OBSERVED_AT,
        legacy_capture_id="20260813T120000Z",
        started_at=OBSERVED_AT,
        ended_at=OBSERVED_AT + timedelta(seconds=1),
        packet_count=1,
        content_digest=CanonicalDigest("1" * 64),
        source_metadata=CaptureSourceMetadata(
            "evidence-fixture.pcap",
            8192,
            "/research/evidence-fixture.pcap",
            False,
        ),
        capture_filter="tcp port 443",
        tool_versions={"jaws": "2.0.0", "tshark": "4.4.0"},
    )
    scopes = (
        ArchivedObservationScope(
            scope_id=scope_id,
            kind=ObservationScopeKind.CAPTURE,
            created_at=OBSERVED_AT,
            capture_ids=(capture_id,),
            filters=("tcp port 443",),
        ),
        ArchivedObservationScope(
            scope_id=ObservationScopeId("scope_legacy_unstamped"),
            kind=ObservationScopeKind.LEGACY,
            created_at=OBSERVED_AT,
            quarantined=True,
        ),
        ArchivedObservationScope(
            scope_id=ObservationScopeId("scope_pooled_all"),
            kind=ObservationScopeKind.POOLED,
            created_at=OBSERVED_AT,
        ),
    )
    packet = PacketRecord(
        capture_id=capture_id,
        observed_at=OBSERVED_AT + timedelta(milliseconds=10),
        protocol="TCP",
        size_bytes=128,
        source_ip="192.0.2.10",
        destination_ip="198.51.100.20",
        source_port=50000,
        destination_port=443,
        payload="00:01",
    )
    orphan_packet = PacketRecord(
        capture_id=CaptureId("cap_evidence_orphan"),
        observed_at=OBSERVED_AT + timedelta(milliseconds=20),
        protocol="UDP",
        size_bytes=64,
        source_ip="198.51.100.20",
        destination_ip="192.0.2.10",
        payload="",
    )
    entities = (
        ArchivedEntity(
            entity_id=source,
            ip_address="192.0.2.10",
            organizations=("Evidence Fixture Networks", "Legacy Fixture Networks"),
            hostname="fixture.example",
            location="Example City",
            coordinates="1.0,2.0",
        ),
        ArchivedEntity(entity_id=destination, ip_address="198.51.100.20"),
    )
    enrichment = EnrichmentRecord(
        entity_id=source,
        ip_address="192.0.2.10",
        status=EnrichmentStatus.SUCCEEDED,
        acquired_at=OBSERVED_AT,
        provider_id="evidence-fixture",
        provider_revision="1",
        organization="Evidence Fixture Networks",
        hostname="fixture.example",
        location="Example City",
        coordinates="1.0,2.0",
        confidence=0.9,
    )
    annotation = ResearcherAnnotation(
        entity_id=source,
        key="evidence_fixture_role",
        value="control",
        author="researcher",
        recorded_at=OBSERVED_AT,
        ground_truth=True,
    )
    current = ArchivedProfile(
        scope_id=scope_id,
        entity_id=source,
        profile_key="profile_evidence_fixture",
        legacy_scope=capture_id.value,
        representation_id="endpoint-description",
        representation_version="1",
        model_id="fixture-model",
        model_revision="fixture-revision",
        computed_at=OBSERVED_AT + timedelta(seconds=2),
        address_classification="documentation",
        organization="Evidence Fixture Networks",
        hostname="fixture.example",
        location="Example City",
        bytes_out=128,
        packets_out=1,
        out_peers=1,
        out_ports=(443,),
        protocols=("TCP",),
        interval_mean=0.1,
        interval_cv=0.0,
        embedding=(0.25, 0.75),
        outlier=OutlierStatus.INLIER,
    )
    quarantined = ArchivedProfile(
        scope_id=ObservationScopeId("scope_legacy_unstamped"),
        entity_id=destination,
        profile_key=None,
        legacy_scope=None,
        representation_id=None,
        representation_version=None,
        model_id=None,
        model_revision=None,
        computed_at=None,
        address_classification=None,
        status=ProfileStatus.LEGACY_QUARANTINED,
    )
    return EvidenceSnapshot(
        schema=schema,
        captures=(capture,),
        observation_scopes=scopes,
        packets=(packet, orphan_packet),
        entities=entities,
        enrichments=(enrichment,),
        annotations=(annotation,),
        profiles=(current, quarantined),
    )


def assert_evidence_transfer_contract(source_repository, target_repository) -> None:
    clock = FrozenClock(OBSERVED_AT + timedelta(minutes=1))
    source_service = EvidenceTransferService(source_repository, clock)
    bundle = source_service.export()
    assert bundle.evidence == evidence_fixture()
    assert bundle.manifest.record_counts["profiles"] == 2
    assert bundle.manifest.content_checksum == bundle.evidence.content_checksum

    target_service = EvidenceTransferService(target_repository, clock)
    dry_run = target_service.dry_run_import(bundle)
    assert not dry_run.applied
    assert dry_run.plan.target_empty
    assert target_repository.is_empty()

    result = target_service.apply_import(bundle, dry_run.plan)
    assert result.applied
    assert target_repository.snapshot() == bundle.evidence
    assert target_repository.snapshot().content_checksum == bundle.evidence.content_checksum

    occupied = target_service.plan_import(bundle)
    assert not occupied.target_empty
    with pytest.raises(EvidenceImportConflictError, match="not empty"):
        target_service.apply_import(bundle, occupied)


def in_memory_source() -> InMemoryEvidenceRepository:
    evidence = evidence_fixture()
    return InMemoryEvidenceRepository(evidence.schema, evidence)


def in_memory_target() -> InMemoryEvidenceRepository:
    return InMemoryEvidenceRepository(schema_provenance())
