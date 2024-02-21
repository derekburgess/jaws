from neo4j import GraphDatabase
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModel

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))
model_name = "bigcode/starcoder"
tokenizer = AutoTokenizer.from_pretrained("bigcode/starcoder", token='KEY')
model = AutoModel.from_pretrained("bigcode/starcoder", token='KEY').to('cuda')

def fetch_data():
    print("\nFetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE p.embedding IS NULL
    WITH src, dst, p
    OPTIONAL MATCH (src)-[:OWNERSHIP]->(org:Organization)
    OPTIONAL MATCH (dst)-[:OWNERSHIP]->(dst_org:Organization)
    RETURN src.address AS src_ip, 
        dst.address AS dst_ip,
        p.src_port AS src_port, 
        p.dst_port AS dst_port, 
        p.src_mac AS src_mac, 
        p.dst_mac AS dst_mac,  
        p.protocol AS protocol,
        p.tcp_flags AS tcp,
        p.size AS size, 
        p.payload AS payload,
        p.payload_ascii AS ascii,
        p.payload_binary AS binary,
        p.http_url AS http, 
        p.dns_domain AS dns,
        org.name AS org,
        org.hostname AS hostname,
        org.location AS location, 
        ID(p) AS packet_id
    LIMIT 1
    """
    with driver.session() as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    print(f"Retrieved {len(df)} record without embeddings.")
    return df

def update_neo4j(packet_id, embedding):
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE ID(p) = $packet_id
    SET p.embedding = $embedding
    """
    with driver.session() as session:
        session.run(query, packet_id=packet_id, embedding=embedding)

def get_embedding(text):
    inputs = tokenizer(text, return_tensors="pt", max_length=512, truncation=True).to('cuda')
    with torch.no_grad():
        outputs = model(**inputs)
    embeddings = outputs.last_hidden_state[:, 0, :].cpu().numpy().tolist()[0]
    return embeddings

def process_embeddings(df):
    for _, row in df.iterrows():
        text = f"{row['src_ip']}:{row['src_port']}({row['src_mac']}) > {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) using: {row['protocol']}({row['tcp']}), sending: [hex: {row['payload']}] [binary: {row['binary']}] at a size of: {row['size']} with ownership: {row['org']}, {row['hostname']}({row['dns']}), {row['location']}"
        embedding = get_embedding(text)
        update_neo4j(row['packet_id'], embedding)
        
        # Reverse direction
        text = f"{row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) > {row['src_ip']}:{row['src_port']}({row['src_mac']}) using: {row['protocol']}({row['tcp']}), sending: [hex: {row['payload']}] [binary: {row['binary']}] at a size of: {row['size']} with ownership: {row['org']}, {row['hostname']}({row['dns']}), {row['location']}"
        embedding = get_embedding(text)
        update_neo4j(row['packet_id'], embedding)

        #print("\nProcessing embedding for...")
        #print(f"Text for node ID {row['packet_id']}: {text}")

while True:
    df = fetch_data()
    if df.empty:
        print("No new node without embeddings. Terminating script...")
        break
    
    process_embeddings(df)
    print("\nFinished processing embedding(s)...")

driver.close()
print("Closed connection to Neo4j.")
