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
        df['payload_binary'] = df['payload'].apply(hex_to_binary)
        
    print(f"Retrieved {len(df)} records.")
    return df

def hex_to_ascii(hex_string):
    print("\nConverting hexadecimal payloads to ASCII...")
    try:
        bytes_list = hex_string.split(':')
        ascii_string = ''.join(chr(int(byte, 16)) for byte in bytes_list)
        return ascii_string
    except ValueError:
        return None

def hex_to_binary(hex_string):
    print("\nConverting hexadecimal payloads to Binary...")
    try:
        bytes_list = hex_string.split(':')
        binary_string = ' '.join(format(int(byte, 16), '08b') for byte in bytes_list)
        return binary_string
    except ValueError:
        return None

def update_neo4j(src_ip, payload, payload_ascii, payload_binary):
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE src.address = $src_ip AND p.payload = $payload
    SET p.payload_ascii = $payload_ascii, p.payload_binary = $payload_binary
    """
    with driver.session() as session:
        session.run(query, src_ip=src_ip, payload=payload, payload_ascii=payload_ascii, payload_binary=payload_binary)

data = fetch_data()
for index, row in data.iterrows():
    src_ip = row['src_ip']
    payload = row['payload']
    payload_ascii = row['payload_ascii']
    payload_binary = row['payload_binary']
    if payload_ascii is not None and payload_binary is not None:
        update_neo4j(src_ip, payload, payload_ascii, payload_binary)
