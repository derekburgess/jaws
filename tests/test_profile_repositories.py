"""Offline enrichment and profile repository contract tests."""

from types import SimpleNamespace

from profile_repository_contract import assert_enrichment_and_profile_repository_contract

from jaws.ports import InMemoryEnrichmentRepository, InMemoryProfileRepository


def test_in_memory_enrichment_and_profile_repositories_follow_contract():
    repositories = SimpleNamespace(
        enrichment=InMemoryEnrichmentRepository(
            addresses=("192.0.2.10", "198.51.100.20"),
            legacy_unknown_address_set={"192.0.2.10"},
        ),
        profiles=InMemoryProfileRepository(),
    )

    assert_enrichment_and_profile_repository_contract(repositories)
