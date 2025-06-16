import argparse
import ipinfo
from rich.live import Live
from rich.console import Group
from jaws.jaws_config import *
from jaws.jaws_utils import (
    dbms_connection,
    render_error_panel,
    render_info_panel,
    render_success_panel,
    render_activity_panel
)


def get_ipinfo(ip_address, ipinfo_api_key):
    try:
        handler = ipinfo.getHandler(ipinfo_api_key)
        details = handler.getDetails(ip_address)
        #print(details.all)
        return details.all
    except Exception as e:
        CONSOLE.print(render_error_panel("ERROR", f"{ip_address} | {e}", CONSOLE))
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
    SET org.HOSTNAME = $hostname, org.LOCATION = $location
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
    parser.add_argument("--agent", action="store_true", help="Disable rich output for agent use.")
    args = parser.parse_args()
    driver = dbms_connection(args.database)
    if driver is None:
        return
    
    ip_addresses = fetch_data_for_organization(driver, args.database)
    if not ip_addresses:
        if not args.agent:
            CONSOLE.print(render_info_panel("INFO", f"No undocumented addresses.", CONSOLE))
            driver.close()
            return
        else:
            return f"[INFO] No undocumented addresses."
    
    organizations = []
    try:
        if not args.agent:
            address_message = f"Found undocumented addresses({len(ip_addresses)})"
            with Live(Group(
                    render_info_panel("CONFIG", address_message, CONSOLE),
                    render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
                ), console=CONSOLE, refresh_per_second=10) as live:
                
                for ip_address in ip_addresses:
                    ipinfo = get_ipinfo(ip_address, IPINFO_API_KEY)
                    if ipinfo:
                        add_organization_to_database(ip_address, ipinfo, driver, args.database)
                        org_name = ipinfo.get('org', ipinfo.get('company', {}).get('name', ipinfo.get('asn', {}).get('name', 'Unknown')))
                        org_string = f"{org_name} ➜ {ip_address}\n{ipinfo.get('hostname', 'Unknown')}, {ipinfo.get('loc', 'Unknown')}\n"
                        organizations.append(org_string)
                        live.update(Group(
                            render_info_panel("CONFIG", address_message, CONSOLE),
                            render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
                        ))

                live.stop()
                CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Organizations({len(organizations)}) added to: '{args.database}'", CONSOLE))
        else:
            for ip_address in ip_addresses:
                ipinfo = get_ipinfo(ip_address, IPINFO_API_KEY)
                if ipinfo:
                    add_organization_to_database(ip_address, ipinfo, driver, args.database)
                    org_name = ipinfo.get('org', ipinfo.get('company', {}).get('name', ipinfo.get('asn', {}).get('name', 'Unknown')))
                    org_string = f"{org_name} ➜ {ip_address}\n{ipinfo.get('hostname', 'Unknown')}, {ipinfo.get('loc', 'Unknown')}\n"
                    organizations.append(org_string)
            print(f"[PROCESS COMPLETE] Organizations({len(organizations)}) added to: '{args.database}'")
        return

    except Exception as e:
        if not args.agent:
            CONSOLE.print(render_error_panel("ERROR", f"{str(e)}", CONSOLE))
            return
        else:
            return f"[ERROR] {str(e)}"
        
    finally:
        driver.close()

if __name__ == "__main__":
    main()