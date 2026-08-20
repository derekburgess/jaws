"""Scoped endpoint inspection over the repository read port."""

from __future__ import annotations

from dataclasses import dataclass, replace

from jaws.domain import (
    CaptureId,
    EndpointInspection,
    EntityId,
    EvidencePointer,
    canonical_digest,
)
from jaws.ports.repositories import InspectionRepository

PORT_HEURISTIC_NOTE = (
    "Service ports are inferred as the lower port in a two-port exchange and ephemeral ports "
    "as the higher port. This is a descriptive heuristic, not proof of client/server roles; "
    "peer-to-peer, active FTP, and nonstandard services can invert it."
)


@dataclass(frozen=True, slots=True)
class InspectionRequest:
    entity_id: EntityId
    capture_id: CaptureId | None = None
    peer_limit: int = 20
    packet_limit: int = 50
    history_limit: int = 20


@dataclass(frozen=True, slots=True)
class ScopedEndpointInspection:
    inspection: EndpointInspection
    profile_scope: str
    all_session_totals: bool
    evidence: EvidencePointer
    port_heuristic_note: str = PORT_HEURISTIC_NOTE


class InspectionService:
    """Select the requested profile while retaining explicitly all-session context."""

    def __init__(self, repository: InspectionRepository) -> None:
        self.repository = repository

    def inspect(self, request: InspectionRequest) -> ScopedEndpointInspection:
        inspection = self.repository.inspect(
            request.entity_id,
            peer_limit=request.peer_limit,
            packet_limit=request.packet_limit,
            history_limit=request.history_limit,
        )
        profile = inspection.profile
        scope = "latest"
        if request.capture_id is not None:
            scope = request.capture_id.value
            candidates = inspection.history + ((inspection.profile,) if inspection.profile else ())
            profile = next(
                (row for row in candidates if row is not None and row.legacy_scope == scope),
                None,
            )
        scoped = replace(inspection, profile=profile)
        evidence = (
            EvidencePointer(
                capture_id=request.capture_id,
                entity_id=request.entity_id,
                selector="endpoint_profile",
            )
            if request.capture_id is not None
            else EvidencePointer(
                artifact_digest=canonical_digest(scoped),
                entity_id=request.entity_id,
                selector="latest_endpoint_profile",
            )
        )
        return ScopedEndpointInspection(
            inspection=scoped,
            profile_scope=scope,
            all_session_totals=True,
            evidence=evidence,
        )
