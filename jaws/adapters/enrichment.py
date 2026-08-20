"""IPinfo-backed provider adapter yielding provider-neutral enrichment observations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from jaws.domain import EnrichmentObservation, EnrichmentStatus, EntityId
from jaws.optional_dependencies import require_module


def _optional_text(value: object) -> str | None:
    text = value.strip() if isinstance(value, str) else None
    return text or None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def ipinfo_revision(package_version: Callable[[str], str] = version) -> str:
    try:
        return package_version("ipinfo")
    except PackageNotFoundError:
        return "runtime-unreported"


def ipinfo_organization(details: Mapping[str, Any]) -> str | None:
    company = _mapping(details.get("company"))
    asn = _mapping(details.get("asn"))
    return (
        _optional_text(details.get("org"))
        or _optional_text(company.get("name"))
        or _optional_text(asn.get("name"))
    )


def ipinfo_location(details: Mapping[str, Any]) -> str | None:
    parts = tuple(
        text
        for key in ("city", "region", "country")
        if (text := _optional_text(details.get(key))) is not None
    )
    return ", ".join(parts) if parts else _optional_text(details.get("loc"))


def _http_status(error: Exception) -> int | None:
    if type(error).__name__ == "RequestQuotaExceededError":
        return 429
    for candidate in (
        getattr(error, "status_code", None),
        getattr(error, "status", None),
        getattr(error, "error_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def _failure(error: Exception, revision: str) -> EnrichmentObservation:
    status_code = _http_status(error)
    if status_code == 404:
        status = EnrichmentStatus.NOT_FOUND
    elif (
        status_code in {408, 425, 429}
        or (status_code is not None and status_code >= 500)
        or (status_code is None and isinstance(error, OSError))
        or "timeout" in type(error).__name__.lower()
    ):
        status = EnrichmentStatus.TRANSIENT_FAILURE
    else:
        status = EnrichmentStatus.PERMANENT_FAILURE
    suffix = str(status_code) if status_code is not None else type(error).__name__.lower()
    return EnrichmentObservation(
        status=status,
        provider_id="ipinfo",
        provider_revision=revision,
        failure_code=f"ipinfo_{suffix}",
    )


@dataclass(slots=True)
class IpinfoEnrichmentProvider:
    """Lazily create one IPinfo handler and normalize all provider outcomes."""

    api_key: Callable[[], str]
    module_loader: Callable[[str, str, str], Any] = require_module
    package_version: Callable[[str], str] = version
    _handler: Any = field(default=None, init=False, repr=False)

    def _client(self) -> Any:
        if self._handler is None:
            module = self.module_loader("ipinfo", "enrichment", "IPinfo enrichment")
            self._handler = module.getHandler(self.api_key())
        return self._handler

    def enrich(self, entity: EntityId) -> EnrichmentObservation:
        address = entity.value.removeprefix("ip:")
        revision = ipinfo_revision(self.package_version)
        client = self._client()
        try:
            response = client.getDetails(address)
            details = _mapping(getattr(response, "all", None))
        except Exception as error:
            return _failure(error, revision)

        organization = ipinfo_organization(details)
        asn = _mapping(details.get("asn"))
        metadata = {
            "organization": organization,
            "asn": _optional_text(asn.get("asn")),
            "hostname": _optional_text(details.get("hostname")),
            "location": ipinfo_location(details),
            "coordinates": _optional_text(details.get("loc")),
        }
        if not any(metadata.values()):
            return EnrichmentObservation(
                status=EnrichmentStatus.NOT_FOUND,
                provider_id="ipinfo",
                provider_revision=revision,
                failure_code="ipinfo_empty_response",
            )
        return EnrichmentObservation(
            status=EnrichmentStatus.SUCCEEDED,
            provider_id="ipinfo",
            provider_revision=revision,
            organization=metadata["organization"],
            asn=metadata["asn"],
            hostname=metadata["hostname"],
            location=metadata["location"],
            coordinates=metadata["coordinates"],
        )
