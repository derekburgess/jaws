"""Legacy jaws-ipinfo output over the provider-neutral enrichment service."""

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

from jaws import jaws_ipinfo
from jaws.domain import EnrichmentObservation, EnrichmentStatus, EntityId
from jaws.ports import (
    FakeEnrichmentProvider,
    FrozenClock,
    InMemoryEnrichmentRepository,
    RecordingWaitStrategy,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


class Driver:
    closed = False

    def close(self):
        self.closed = True


class Reporter:
    def __init__(self):
        self.results = []
        self.infos = []
        self.errors = []

    def info(self, title, message):
        self.infos.append((title, message))

    def error(self, title, message):
        self.errors.append((title, message))

    def result(self, value, summary=None):
        self.results.append((value, summary))

    @contextmanager
    def activity(self, render):
        yield lambda: None


@dataclass(slots=True)
class ScriptedProvider:
    responses: dict[EntityId, list[EnrichmentObservation]]
    requests: list[EntityId] = field(default_factory=list)

    def enrich(self, entity):
        self.requests.append(entity)
        return self.responses[entity].pop(0)


def _runtime(monkeypatch, repository, provider):
    driver = Driver()
    reporter = Reporter()
    monkeypatch.setattr(jaws_ipinfo, "Reporter", lambda: reporter)
    monkeypatch.setattr(jaws_ipinfo, "dbms_connection", lambda database, reporter: driver)
    monkeypatch.setattr(
        jaws_ipinfo,
        "Neo4jRepositories",
        SimpleNamespace(connect=lambda driver, database: SimpleNamespace(enrichment=repository)),
    )
    monkeypatch.setattr(jaws_ipinfo, "IpinfoEnrichmentProvider", lambda api_key: provider)
    monkeypatch.setattr(jaws_ipinfo, "SystemClock", lambda: FrozenClock(NOW))
    monkeypatch.setattr(sys, "argv", ["jaws-ipinfo", "--database", "fixtures"])
    return driver, reporter


def test_cli_preserves_structured_result_shape_over_enrichment_service(monkeypatch):
    repository = InMemoryEnrichmentRepository(addresses=("10.0.0.1", "8.8.8.8"))
    provider = FakeEnrichmentProvider(
        {
            EntityId("ip:8.8.8.8"): EnrichmentObservation(
                status=EnrichmentStatus.SUCCEEDED,
                provider_id="fixture",
                provider_revision="1",
                organization="Google LLC",
                hostname="dns.google",
                location="US",
            )
        }
    )
    driver, reporter = _runtime(monkeypatch, repository, provider)

    jaws_ipinfo.main()

    assert reporter.results[0][0] == {
        "database": "fixtures",
        "addresses_scanned": 1,
        "addresses_skipped_non_public": 1,
        "addresses_already_documented": 0,
        "organizations_added": 1,
    }
    assert reporter.errors == []
    assert driver.closed


def test_cli_does_not_require_provider_runtime_when_only_non_public_addresses_remain(monkeypatch):
    repository = InMemoryEnrichmentRepository(addresses=("10.0.0.1",))
    provider = FakeEnrichmentProvider({})
    driver, reporter = _runtime(monkeypatch, repository, provider)

    jaws_ipinfo.main()

    assert reporter.results[0][0]["addresses_scanned"] == 0
    assert reporter.results[0][0]["addresses_skipped_non_public"] == 1
    assert provider.requests == []
    assert reporter.errors == []
    assert driver.closed


def test_cli_runtime_retry_overrides_drive_the_injected_wait_policy(monkeypatch):
    entity = EntityId("ip:8.8.8.8")
    repository = InMemoryEnrichmentRepository(addresses=("8.8.8.8",))
    provider = ScriptedProvider(
        {
            entity: [
                EnrichmentObservation(
                    status=EnrichmentStatus.TRANSIENT_FAILURE,
                    provider_id="fixture",
                    provider_revision="1",
                    failure_code="fixture_429",
                ),
                EnrichmentObservation(
                    status=EnrichmentStatus.SUCCEEDED,
                    provider_id="fixture",
                    provider_revision="1",
                    organization="Google LLC",
                ),
            ]
        }
    )
    waits = RecordingWaitStrategy()
    driver, reporter = _runtime(monkeypatch, repository, provider)
    monkeypatch.setattr(jaws_ipinfo, "SystemWaitStrategy", lambda: waits)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "jaws-ipinfo",
            "--database",
            "fixtures",
            "--max-attempts",
            "2",
            "--request-interval",
            "0.1",
            "--initial-backoff",
            "0.75",
            "--backoff-multiplier",
            "3",
            "--max-backoff",
            "1",
        ],
    )

    jaws_ipinfo.main()

    assert provider.requests == [entity, entity]
    assert waits.delays == [0.75]
    assert reporter.results[0][0]["organizations_added"] == 1
    assert reporter.errors == []
    assert driver.closed
