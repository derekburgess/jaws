"""Thin compatibility CLI for provider-neutral IP enrichment acquisition."""

import argparse

from rich.console import Group

from jaws.adapters import (
    IpinfoEnrichmentProvider,
    SystemClock,
    SystemWaitStrategy,
    ipinfo_location,
    ipinfo_organization,
)
from jaws.adapters import ipinfo_revision as ipinfo_revision
from jaws.config import CONSOLE, DATABASE, get_ipinfo_api_key
from jaws.domain import EnrichmentRecord, EnrichmentStatus
from jaws.jaws_utils import Reporter, dbms_connection, render_activity_panel, render_info_panel
from jaws.services import (
    DEFAULT_ENRICHMENT_ACQUISITION_POLICY,
    EnrichmentAcquisitionPolicy,
    EnrichmentService,
)
from jaws.services import cleanup_legacy_unknown as cleanup_unknown
from jaws.storage import Neo4jEnrichmentRepository, Neo4jRepositories


def fetch_data_for_organization(driver, database, repository=None):
    """Compatibility projection of the repository's deterministic pending inventory."""

    repository = repository or Neo4jEnrichmentRepository(driver, database)
    return list(repository.pending_addresses())


def fetch_total_addresses(driver, database, repository=None):
    """Compatibility projection of the repository's entity count."""

    repository = repository or Neo4jEnrichmentRepository(driver, database)
    return repository.count_entities()


def cleanup_legacy_unknown(driver, database, repository=None):
    """Compatibility shim; deterministic cleanup now belongs to EnrichmentService."""

    repository = repository or Neo4jEnrichmentRepository(driver, database)
    return cleanup_unknown(repository)


def format_location(details):
    """Compatibility display helper retaining the historical unknown fallback."""

    return ipinfo_location(details) or "Unknown"


def organization_name(details):
    """Compatibility display helper retaining the historical unknown fallback."""

    return ipinfo_organization(details) or "Unknown"


def _display_record(record: EnrichmentRecord) -> str:
    organization = record.organization or "Unknown"
    hostname = record.hostname or "Unknown"
    location = record.location or record.coordinates or "Unknown"
    return f"{organization} ➜ {record.ip_address}\n{hostname}, {location}\n"


def main():
    parser = argparse.ArgumentParser(
        description="Update the database with IP organization information from Ipinfo."
    )
    parser.add_argument(
        "--database",
        default=DATABASE,
        help=f"Specify the database to connect to (default: '{DATABASE}').",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_ENRICHMENT_ACQUISITION_POLICY.max_attempts,
        help="Maximum provider attempts per public address.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=DEFAULT_ENRICHMENT_ACQUISITION_POLICY.minimum_request_interval_seconds,
        help="Minimum seconds between provider requests.",
    )
    parser.add_argument(
        "--initial-backoff",
        type=float,
        default=DEFAULT_ENRICHMENT_ACQUISITION_POLICY.initial_backoff_seconds,
        help="Seconds to wait before the first transient retry.",
    )
    parser.add_argument(
        "--backoff-multiplier",
        type=float,
        default=DEFAULT_ENRICHMENT_ACQUISITION_POLICY.backoff_multiplier,
        help="Multiplier applied to each subsequent retry delay.",
    )
    parser.add_argument(
        "--max-backoff",
        type=float,
        default=DEFAULT_ENRICHMENT_ACQUISITION_POLICY.maximum_backoff_seconds,
        help="Maximum seconds for any transient retry delay.",
    )
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    try:
        repository = Neo4jRepositories.connect(driver, args.database).enrichment
    except Exception as error:
        reporter.error("ERROR", str(error))
        driver.close()
        return

    organizations: list[str] = []

    def render():
        return Group(
            render_info_panel("CONFIG", "Enriching undocumented public addresses", CONSOLE),
            render_activity_panel("ORGANIZATIONS", organizations, CONSOLE),
        )

    try:
        with reporter.activity(render) as update:

            def on_record(record: EnrichmentRecord) -> None:
                if record.status is EnrichmentStatus.SUCCEEDED:
                    organizations.append(_display_record(record))
                    update()
                elif record.status is not EnrichmentStatus.NOT_APPLICABLE:
                    reporter.info(
                        "WARNING",
                        f"{record.ip_address} | {record.status.value}: {record.failure_code}",
                    )

            result = EnrichmentService(
                repository=repository,
                provider=IpinfoEnrichmentProvider(get_ipinfo_api_key),
                clock=SystemClock(),
                policy=EnrichmentAcquisitionPolicy(
                    max_attempts=args.max_attempts,
                    minimum_request_interval_seconds=args.request_interval,
                    initial_backoff_seconds=args.initial_backoff,
                    backoff_multiplier=args.backoff_multiplier,
                    maximum_backoff_seconds=args.max_backoff,
                ),
                wait_strategy=SystemWaitStrategy(),
            ).enrich_pending(observer=on_record)

        output = {
            "database": args.database,
            "addresses_scanned": result.addresses_scanned,
            "addresses_skipped_non_public": result.addresses_skipped_non_public,
            "addresses_already_documented": result.addresses_already_documented,
            "organizations_added": result.organizations_added,
        }
        if result.addresses_scanned == 0:
            summary = (
                "No undocumented public addresses "
                f"({result.addresses_skipped_non_public} non-public skipped, "
                f"{result.addresses_already_documented} already documented)."
            )
        else:
            summary = (
                f"Organizations({result.organizations_added}) added to: '{args.database}' "
                f"({result.addresses_skipped_non_public} non-public skipped, "
                f"{result.addresses_already_documented} already documented)"
            )
        reporter.result(output, summary=summary)
    except Exception as error:
        reporter.error("ERROR", str(error))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
