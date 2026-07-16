import argparse
import ipinfo
from rich.console import Group
from jaws.config import CONSOLE, DATABASE, IPINFO_API_KEY
from jaws.jaws_utils import (
    dbms_connection,
    Reporter,
    render_info_panel,
    render_activity_panel,
    classify_endpoint
)


def get_ipinfo(handler, ip_address, reporter):
    try:
        details = handler.getDetails(ip_address)
        return details.all
    except Exception as e:
        # Non-fatal: one IP failed to resolve, the run continues. Narrate (stderr in
        # agent mode) rather than emit a structured error onto the result surface.
        reporter.info("WARNING", f"{ip_address} | {e}")
        return None


def fetch_data_for_organization(driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS)
    WHERE NOT (ip_address)<-[:OWNERSHIP]-(:ORGANIZATION)
    RETURN DISTINCT ip_address.IP_ADDRESS AS ip_address
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        return [record['ip_address'] for record in result]


def fetch_total_addresses(driver, database):
    query = "MATCH (ip_address:IP_ADDRESS) RETURN count(ip_address) AS total"
    with driver.session(database=database) as session:
        return session.run(query).single()["total"]


def cleanup_legacy_unknown(driver, database):
    """Detach non-public IPs from the legacy 'Unknown' organization.

    Before the non-public skip existed, bogon lookups all merged into a single
    'Unknown' org node. Those edges make old graphs read as already-documented, so
    the addresses_skipped_non_public counter under-reports on them. Classification
    happens here (classify_endpoint is Python); the org node is deleted when the
    detach orphans it. Returns how many IPs were detached.
    """
    fetch_query = """
    MATCH (:ORGANIZATION {ORGANIZATION: 'Unknown'})-[:OWNERSHIP]->(ip:IP_ADDRESS)
    RETURN ip.IP_ADDRESS AS ip_address
    """
    detach_query = """
    MATCH (org:ORGANIZATION {ORGANIZATION: 'Unknown'})-[r:OWNERSHIP]->(ip:IP_ADDRESS)
    WHERE ip.IP_ADDRESS IN $ips
    DELETE r
    WITH DISTINCT org
    WHERE NOT (org)-[:OWNERSHIP]->()
    DELETE org
    """
    with driver.session(database=database) as session:
        documented = [r["ip_address"] for r in session.run(fetch_query)]
        stale = [ip for ip in documented if classify_endpoint(ip) != "public"]
        if stale:
            session.run(detach_query, {"ips": stale})
    return len(stale)


def format_location(ipinfo):
    """Human location "City, Region, Country", falling back to raw coordinates.

    City names carry semantic signal downstream (they are embedded into the endpoint
    profile text and shown in reports) where bare lat/long carries none; the raw
    coordinates are kept separately as COORDINATES for anything geometric.
    """
    parts = [ipinfo.get(k) for k in ("city", "region", "country")]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else ipinfo.get("loc", "Unknown")


def add_organization_to_database(ip_address, ipinfo, driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (org:ORGANIZATION {ORGANIZATION: $org})
    MERGE (ip_address)<-[:OWNERSHIP]-(org)
    SET ip_address.HOSTNAME = $hostname, ip_address.LOCATION = $location,
        ip_address.COORDINATES = $coordinates
    """
    with driver.session(database=database) as session:
        session.run(query, {
            'ip_address': ip_address,
            'org': ipinfo.get('org', ipinfo.get('company', {}).get('name', ipinfo.get('asn', {}).get('name', 'Unknown'))),
            'hostname': ipinfo.get('hostname', 'Unknown'),
            'location': format_location(ipinfo),
            'coordinates': ipinfo.get('loc', 'Unknown')
        })
        

def main():
    parser = argparse.ArgumentParser(description="Update the database with IP organization information from Ipinfo.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    # Old graphs carry OWNERSHIP edges from non-public IPs to a legacy 'Unknown' org
    # (created before the non-public skip existed); detach them first so those IPs
    # count as skipped below rather than silently reading as already-documented.
    cleanup_legacy_unknown(driver, args.database)
    total_addresses = fetch_total_addresses(driver, args.database)
    undocumented = fetch_data_for_organization(driver, args.database)
    # Every IP_ADDRESS node is accounted for in the result: scanned + skipped +
    # already_documented == the graph's total, so the counters are auditable.
    already_documented = total_addresses - len(undocumented)
    # Only public (globally routable) IPs get a lookup — private/multicast/link-local
    # addresses can only resolve as bogons, which previously all merged into a single
    # 'Unknown' organization node. Skipped IPs stay undocumented (no OWNERSHIP edge)
    # and are re-skipped cheaply on every run.
    ip_addresses = [ip for ip in undocumented if classify_endpoint(ip) == "public"]
    skipped_non_public = len(undocumented) - len(ip_addresses)
    if not ip_addresses:
        reporter.result(
            {
                "database": args.database,
                "addresses_scanned": 0,
                "addresses_skipped_non_public": skipped_non_public,
                "addresses_already_documented": already_documented,
                "organizations_added": 0,
            },
            summary=f"No undocumented public addresses ({skipped_non_public} non-public skipped, {already_documented} already documented).",
        )
        driver.close()
        return

    organizations = []
    address_message = f"Found undocumented public addresses({len(ip_addresses)}), skipping non-public({skipped_non_public})"

    def render():
        return Group(
            render_info_panel("CONFIG", address_message, CONSOLE),
            render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
        )

    try:
        # One handler for the whole run — it caches lookups internally, which a
        # per-IP handler would defeat.
        handler = ipinfo.getHandler(IPINFO_API_KEY)
        with reporter.activity(render) as update:
            for ip_address in ip_addresses:
                ipinfo_details = get_ipinfo(handler, ip_address, reporter)
                if ipinfo_details:
                    add_organization_to_database(ip_address, ipinfo_details, driver, args.database)
                    org_name = ipinfo_details.get('org', ipinfo_details.get('company', {}).get('name', ipinfo_details.get('asn', {}).get('name', 'Unknown')))
                    # The full org→IP→hostname→loc detail is queryable via fetch_traffic;
                    # here we only stream a human view (pretty mode) and return a count.
                    org_string = f"{org_name} ➜ {ip_address}\n{ipinfo_details.get('hostname', 'Unknown')}, {format_location(ipinfo_details)}\n"
                    organizations.append(org_string)
                    update()
        # `addresses_scanned` is the denominator — the undocumented public IPs looked
        # up this run — so a reader can see `organizations_added` is an incremental
        # count (this run only), not a running total of all endpoints. Any gap is
        # IPs that failed to resolve, not data loss; non-public IPs are counted
        # separately and never looked up.
        reporter.result(
            {
                "database": args.database,
                "addresses_scanned": len(ip_addresses),
                "addresses_skipped_non_public": skipped_non_public,
                "addresses_already_documented": already_documented,
                "organizations_added": len(organizations),
            },
            summary=f"Organizations({len(organizations)}) added to: '{args.database}' ({skipped_non_public} non-public skipped, {already_documented} already documented)",
        )

    except Exception as e:
        reporter.error("ERROR", str(e))

    finally:
        driver.close()

if __name__ == "__main__":
    main()