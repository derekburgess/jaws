"""Offline contract coverage for the in-memory inspection adapter."""

from types import SimpleNamespace

from inspection_repository_contract import assert_inspection_repository_contract

from jaws.ports import (
    InMemoryCaptureRepository,
    InMemoryEnrichmentRepository,
    InMemoryInspectionRepository,
    InMemoryPacketRepository,
    InMemoryProfileRepository,
)


def test_in_memory_inspection_repository_follows_shared_contract():
    captures = InMemoryCaptureRepository()
    packets = InMemoryPacketRepository(captures)
    enrichment = InMemoryEnrichmentRepository(
        addresses=("192.0.2.10", "198.51.100.20", "203.0.113.30")
    )
    profiles = InMemoryProfileRepository()
    repositories = SimpleNamespace(
        captures=captures,
        packets=packets,
        enrichment=enrichment,
        profiles=profiles,
        inspection=InMemoryInspectionRepository(profiles, packets, enrichment, captures),
    )

    assert_inspection_repository_contract(repositories)
