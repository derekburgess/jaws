from neo4j import GraphDatabase
import requests

uri = "bolt://localhost:7687"  # Typical/local Neo4j URI - Updated as needed
username = "neo4j"  # Typical/local Neo4j username - Updated as needed
password = "testtest"  # Typical/l Neo4j password - Updated as needed
driver = GraphDatabase.driver(uri, auth=(username, password)) # Set up the driver
api_key = 'KEY'  # Replace with your IPinfo key

def get_ip_info(ip_address, api_key):
    general_info_url = f"https://ipinfo.io/{ip_address}/json"
    headers = {'Authorization': f'Bearer {api_key}'}
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

def fetch_data():
    print("\nFetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    RETURN DISTINCT src.address AS src_ip
    """
    with driver.session(database="ethcaptures") as session: # Update database="" to your database name
        result = session.run(query)
        return [record['src_ip'] for record in result]

def update_neo4j(src_ip, ip_info, driver):
    query = """
    MATCH (src:IP {address: $src_ip})
    MERGE (org:Organization {name: $org})
    MERGE (src)-[:OWNERSHIP]->(org)
    SET org.hostname = $hostname, org.location = $location
    """
    with driver.session(database="ethcaptures") as session: # Update database="" to your database name
        session.run(query, {
            'src_ip': src_ip,
            'org': ip_info.get('org', 'None'),
            'hostname': ip_info.get('hostname', 'None'),
            'location': ip_info.get('loc', 'None')
        })

def main(api_key):
    src_ips = fetch_data()
    for src_ip in src_ips:
        ip_info = get_ip_info(src_ip, api_key)
        if ip_info:
            update_neo4j(src_ip, ip_info, driver)
            print(f"Updated IP info for {src_ip}, with: {ip_info.get('org', 'None')}, {ip_info.get('hostname', 'None')}, {ip_info.get('loc', 'None')}")

if __name__ == "__main__":
    main(api_key)