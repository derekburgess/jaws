from neo4j import GraphDatabase
import pandas as pd

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data():
    print("\nFetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    RETURN DISTINCT src.address AS src_ip, p.payload AS payload
    """
    with driver.session() as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
        df['payload_ascii'] = df['payload'].apply(hex_to_ascii)
        
    print(f"Retrieved {len(df)} records.")
    return df

def hex_to_ascii(hex_string):
    print("\nConverting hexidecimal payloads to ASCII...")
    try:
        bytes_list = hex_string.split(':')
        ascii_string = ''.join(chr(int(byte, 16)) for byte in bytes_list)
        #print(f"Payload: {hex_string}, ASCII: {ascii_string}")
        return ascii_string
    except ValueError:
        #print(f"Invalid payload: {hex_string}")
        return None

def update_neo4j(src_ip, payload, payload_ascii):
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE src.address = $src_ip AND p.payload = $payload
    SET p.payload_ascii = $payload_ascii
    """
    with driver.session() as session:
        session.run(query, src_ip=src_ip, payload=payload, payload_ascii=payload_ascii)

data = fetch_data()
for index, row in data.iterrows():
    src_ip = row['src_ip']
    payload = row['payload']
    payload_ascii = row['payload_ascii']
    if payload_ascii is not None:
        update_neo4j(src_ip, payload, payload_ascii)

