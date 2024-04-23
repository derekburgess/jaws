import os
import argparse
from neo4j import GraphDatabase
import requests


uri = os.getenv("LOCAL_NEO4J_URI")
username = os.getenv("LOCAL_NEO4J_USERNAME")
password = os.getenv("LOCAL_NEO4J_PASSWORD")
ipinfo_api_key = os.getenv("IPINFO_API_KEY")


def connect_to_database(uri, username, password, database):
    return GraphDatabase.driver(uri, auth=(username, password))


def get_ip_info(ip_address, ipinfo_api_key):
    general_info_url = f"https://ipinfo.io/{ip_address}/json"
    headers = {'Authorization': f'Bearer {ipinfo_api_key}'}
    ip_info = {}

    try:
        response = requests.get(general_info_url, headers=headers)
        if response.status_code == 200:
            ip_info.update(response.json())
        else:
            print(f"Error fetching general info for {ip_address}: {response.status_code}")
    except requests.RequestException as e:
        print(f"Request failed for {ip_address}: {e}")

    if ip_info:
        return ip_info
    else:
        return None


def fetch_data(driver, database):
    #print("Fetching data from Neo4j...")
    query = """
    MATCH (ip:IP)
    RETURN DISTINCT ip.address AS ip_address
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        return [record['ip_address'] for record in result]


def update_neo4j(ip_address, ip_info, driver, database):
    query = """
    MATCH (ip:IP {address: $ip_address})
    MERGE (org:Organization {name: $org})
    MERGE (ip)-[:OWNERSHIP]->(org)
    SET org.hostname = $hostname, org.location = $location
    """
    with driver.session(database=database) as session:
        session.run(query, {
            'ip_address': ip_address,
            'org': ip_info.get('org', 'None'),
            'hostname': ip_info.get('hostname', 'None'),
            'location': ip_info.get('loc', 'None')
        })


def main():
    parser = argparse.ArgumentParser(description="Update Neo4j database with IP information from ipinfo.io")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")
    
    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)
    ip_addresses = fetch_data(driver, args.database)
    for ip_address in ip_addresses:
        ip_info = get_ip_info(ip_address, ipinfo_api_key)
        if ip_info:
            update_neo4j(ip_address, ip_info, driver, args.database)
            print(f"Created OWNERSHIP realtionship from {ip_address}: {ip_info.get('org', 'None')}, {ip_info.get('hostname', 'None')}, {ip_info.get('loc', 'None')}")

if __name__ == "__main__":
    main()
