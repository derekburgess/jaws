import argparse
import ipinfo
from rich.console import Group
from jaws.config import CONSOLE, DATABASE, IPINFO_API_KEY
from jaws.jaws_utils import (
    dbms_connection,
    Reporter,
    render_info_panel,
    render_activity_panel
)


def get_ipinfo(ip_address, ipinfo_api_key, reporter):
    try:
        handler = ipinfo.getHandler(ipinfo_api_key)
        details = handler.getDetails(ip_address)
        #print(details.all)
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


def add_organization_to_database(ip_address, ipinfo, driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (org:ORGANIZATION {ORGANIZATION: $org})
    MERGE (ip_address)<-[:OWNERSHIP]-(org)
    SET ip_address.HOSTNAME = $hostname, ip_address.LOCATION = $location
    """
    with driver.session(database=database) as session:
        session.run(query, {
            'ip_address': ip_address,
            'org': ipinfo.get('org', ipinfo.get('company', {}).get('name', ipinfo.get('asn', {}).get('name', 'Unknown'))),
            'hostname': ipinfo.get('hostname', 'Unknown'),
            'location': ipinfo.get('loc', 'Unknown')
        })
        

def main():
    parser = argparse.ArgumentParser(description="Update the database with IP organization information from Ipinfo.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    ip_addresses = fetch_data_for_organization(driver, args.database)
    if not ip_addresses:
        reporter.result({"database": args.database, "organizations_added": 0}, summary="No undocumented addresses.")
        driver.close()
        return

    organizations = []
    address_message = f"Found undocumented addresses({len(ip_addresses)})"

    def render():
        return Group(
            render_info_panel("CONFIG", address_message, CONSOLE),
            render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
        )

    try:
        with reporter.activity(render) as update:
            for ip_address in ip_addresses:
                ipinfo = get_ipinfo(ip_address, IPINFO_API_KEY, reporter)
                if ipinfo:
                    add_organization_to_database(ip_address, ipinfo, driver, args.database)
                    org_name = ipinfo.get('org', ipinfo.get('company', {}).get('name', ipinfo.get('asn', {}).get('name', 'Unknown')))
                    # The full org→IP→hostname→loc detail is queryable via fetch_traffic;
                    # here we only stream a human view (pretty mode) and return a count.
                    org_string = f"{org_name} ➜ {ip_address}\n{ipinfo.get('hostname', 'Unknown')}, {ipinfo.get('loc', 'Unknown')}\n"
                    organizations.append(org_string)
                    update()
        reporter.result(
            {"database": args.database, "organizations_added": len(organizations)},
            summary=f"Organizations({len(organizations)}) added to: '{args.database}'",
        )

    except Exception as e:
        reporter.error("ERROR", str(e))

    finally:
        driver.close()

if __name__ == "__main__":
    main()