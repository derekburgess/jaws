import os
import argparse
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
import requests

uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
ipinfo_api_key = os.getenv("IPINFO_API_KEY")

def connect_to_database(uri, username, password, database):
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))
        with driver.session(database=database) as session:
            session.run("RETURN 1")
        return driver
    except ServiceUnavailable:
        raise Exception(f"Unable to connect to Neo4j database. Please check your connection settings.")
    except Exception as e:
        if "database does not exist" in str(e).lower():
            raise Exception(f"{database} database not found. You need to create the default 'captures' database or pass an existing database name.")
        else:
            raise

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

def fetch_data(driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS)
    WHERE NOT (ip_address)<-[:OWNERSHIP]-(:ORGANIZATION)
    RETURN DISTINCT ip_address.IP_ADDRESS AS ip_address
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        return [record['ip_address'] for record in result]

def update_neo4j(ip_address, ip_info, driver, database):
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
    parser = argparse.ArgumentParser(description="Update Neo4j database with IP organization information from ipinfo.")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures).")
    
    args = parser.parse_args()

    try:
        driver = connect_to_database(uri, username, password, args.database)
    except Exception as e:
        print(f"\n{str(e)}\n")
        return

    ip_addresses = fetch_data(driver, args.database)
    print(f"\nFound {len(ip_addresses)} IP addresses without organization nodes", "\n")
    for ip_address in ip_addresses:
        ip_info = get_ip_info(ip_address, ipinfo_api_key)
        if ip_info:
            update_neo4j(ip_address, ip_info, driver, args.database)
            print(f"{ip_address} <- ORGANIZATION: {ip_info.get('org', 'Unknown')}, {ip_info.get('hostname', 'Unknown')}, {ip_info.get('loc', 'Unknown')}")

    driver.close()

if __name__ == "__main__":
    main()