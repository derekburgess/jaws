import argparse
import requests
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


def get_ip_info(ip_address, ipinfo_api_key):
    general_info_url = f"https://ipinfo.io/{ip_address}/json"
    headers = {'Authorization': f'Bearer {ipinfo_api_key}'}
    try:
        response = requests.get(general_info_url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error fetching info for {ip_address}: {response.status_code}")
    except requests.RequestException as e:
        print(f"Request failed for {ip_address}: {e}")
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


def add_organization_to_database(ip_address, ip_info, driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (org:ORGANIZATION {ORGANIZATION: $org})
    MERGE (ip_address)<-[:OWNERSHIP]-(org)
    SET org.HOSTNAME = $hostname, org.LOCATION = $location
    """
    with driver.session(database=database) as session:
        session.run(query, {
            'ip_address': ip_address,
            'org': ip_info.get('org', 'Unknown'),
            'hostname': ip_info.get('hostname', 'Unknown'),
            'location': ip_info.get('loc', 'Unknown')
        })
        

def main():
    parser = argparse.ArgumentParser(description="Update the database with IP organization information from Ipinfo.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--agent", action="store_true", help="Disable rich output for agent use.")
    args = parser.parse_args()
    driver = dbms_connection(args.database)
    ip_addresses = fetch_data_for_organization(driver, args.database)
    organizations = []
    
    if not ip_addresses:
        if not args.agent:
            CONSOLE.print(render_info_panel("INFO", f"No undocumented addresses.", CONSOLE))
            driver.close()
            return
        else:
            return f"\n[INFO] No undocumented addresses.\n"
    
    try:
        if not args.agent:
            address_message = f"Found undocumented addresses({len(ip_addresses)})"
            with Live(Group(
                    render_info_panel("CONFIG", address_message, CONSOLE),
                    render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
                ), console=CONSOLE, refresh_per_second=10) as live:
                
                for ip_address in ip_addresses:
                    ip_info = get_ip_info(ip_address, IPINFO_API_KEY)
                    if ip_info:
                        add_organization_to_database(ip_address, ip_info, driver, args.database)
                        org_string = f"{ip_info.get('org', 'Unknown')} ➜ {ip_address}\n{ip_info.get('hostname', 'Unknown')}, {ip_info.get('loc', 'Unknown')}\n"
                        organizations.append(org_string)
                        live.update(Group(
                            render_info_panel("CONFIG", address_message, CONSOLE),
                            render_activity_panel("ORGANIZATIONS", organizations, CONSOLE)
                        ))

                live.stop()
                CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Organizations({len(organizations)}) added to: '{args.database}'", CONSOLE))
        else:
            for ip_address in ip_addresses:
                ip_info = get_ip_info(ip_address, IPINFO_API_KEY)
                if ip_info:
                    add_organization_to_database(ip_address, ip_info, driver, args.database)
                    org_string = f"{ip_info.get('org', 'Unknown')} ➜ {ip_address}\n{ip_info.get('hostname', 'Unknown')}, {ip_info.get('loc', 'Unknown')}\n"
                    organizations.append(org_string)
            print(f"\n[PROCESS COMPLETE] Organizations({len(organizations)}) added to: '{args.database}'\n")
        return

    except Exception as e:
        if not args.agent:
            CONSOLE.print(render_error_panel("ERROR", f"An error occurred: {str(e)}", CONSOLE))
            return
        else:
            return f"\n[ERROR] An error occurred: {str(e)}\n"
        
    finally:
        driver.close()

if __name__ == "__main__":
    main()