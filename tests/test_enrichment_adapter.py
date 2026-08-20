"""IPinfo response normalization, lazy initialization, and failure taxonomy."""

from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

import pytest

from jaws.adapters import (
    IpinfoEnrichmentProvider,
    ipinfo_location,
    ipinfo_organization,
    ipinfo_revision,
)
from jaws.domain import EnrichmentStatus, EntityId


class Handler:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def getDetails(self, address):
        self.requests.append(address)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(all=self.response)


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class ApiError(Exception):
    def __init__(self, error_code):
        super().__init__(f"API {error_code}")
        self.error_code = error_code


class RequestQuotaExceededError(Exception):
    pass


class TimeoutExceededError(Exception):
    pass


def _provider(handler, calls):
    def loader(module, extra, capability):
        calls.append((module, extra, capability))
        return SimpleNamespace(getHandler=lambda key: handler)

    return IpinfoEnrichmentProvider(
        lambda: "fixture-api-key",
        module_loader=loader,
        package_version=lambda name: "5.1.1",
    )


def test_ipinfo_adapter_lazily_reuses_handler_and_normalizes_meaningful_metadata():
    handler = Handler(
        {
            "company": {"name": "Example Networks"},
            "asn": {"asn": "AS64500", "name": "Fallback ASN"},
            "hostname": " edge.example ",
            "city": "New York",
            "region": "New York",
            "country": "US",
            "loc": "40.7,-74.0",
        }
    )
    calls = []
    provider = _provider(handler, calls)
    assert calls == []

    first = provider.enrich(EntityId("ip:8.8.8.8"))
    second = provider.enrich(EntityId("ip:1.1.1.1"))

    assert first.status is EnrichmentStatus.SUCCEEDED
    assert first.organization == "Example Networks"
    assert first.asn == "AS64500"
    assert first.hostname == "edge.example"
    assert first.location == "New York, New York, US"
    assert first.coordinates == "40.7,-74.0"
    assert second.status is EnrichmentStatus.SUCCEEDED
    assert calls == [("ipinfo", "enrichment", "IPinfo enrichment")]
    assert handler.requests == ["8.8.8.8", "1.1.1.1"]


def test_ipinfo_helpers_never_invent_shared_unknown_metadata():
    assert ipinfo_organization({}) is None
    assert ipinfo_organization({"asn": {"name": "ASN Name"}}) == "ASN Name"
    assert ipinfo_location({}) is None
    assert ipinfo_location({"loc": "1,2"}) == "1,2"
    assert ipinfo_revision(lambda name: "5.1.1") == "5.1.1"

    def missing(name):
        raise PackageNotFoundError(name)

    assert ipinfo_revision(missing) == "runtime-unreported"


def test_empty_provider_response_is_explicit_not_found():
    result = _provider(Handler({}), []).enrich(EntityId("ip:8.8.8.8"))

    assert result.status is EnrichmentStatus.NOT_FOUND
    assert result.failure_code == "ipinfo_empty_response"


@pytest.mark.parametrize(
    ("error", "status", "failure_code"),
    (
        (HttpError(404), EnrichmentStatus.NOT_FOUND, "ipinfo_404"),
        (HttpError(429), EnrichmentStatus.TRANSIENT_FAILURE, "ipinfo_429"),
        (HttpError(503), EnrichmentStatus.TRANSIENT_FAILURE, "ipinfo_503"),
        (ApiError(503), EnrichmentStatus.TRANSIENT_FAILURE, "ipinfo_503"),
        (RequestQuotaExceededError(), EnrichmentStatus.TRANSIENT_FAILURE, "ipinfo_429"),
        (
            TimeoutExceededError(),
            EnrichmentStatus.TRANSIENT_FAILURE,
            "ipinfo_timeoutexceedederror",
        ),
        (ConnectionError("offline"), EnrichmentStatus.TRANSIENT_FAILURE, "ipinfo_connectionerror"),
        (HttpError(401), EnrichmentStatus.PERMANENT_FAILURE, "ipinfo_401"),
    ),
)
def test_provider_failures_map_to_explicit_retry_semantics(error, status, failure_code):
    result = _provider(Handler(error=error), []).enrich(EntityId("ip:8.8.8.8"))

    assert result.status is status
    assert result.failure_code == failure_code


def test_configuration_and_dependency_errors_are_not_misrecorded_as_provider_outcomes():
    provider = IpinfoEnrichmentProvider(
        lambda: "fixture",
        module_loader=lambda *args: (_ for _ in ()).throw(ModuleNotFoundError("ipinfo")),
    )

    with pytest.raises(ModuleNotFoundError, match="ipinfo"):
        provider.enrich(EntityId("ip:8.8.8.8"))
