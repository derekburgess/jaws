from neo4j import GraphDatabase
import pandas as pd
from openai import OpenAI

uri = "bolt://localhost:7687"
username = "neo4j"
password = "testtest"
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data():
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE src.embedding IS NULL OR dst.embedding IS NULL
    RETURN src, dst, p.protocol AS protocol, p.tcp_flags AS tcp_flags, src.address AS src_ip, dst.address AS dst_ip, p.src_port AS src_port, p.dst_port AS dst_port, p.src_mac AS src_mac, p.dst_mac AS dst_mac, p.size AS size, ID(src) AS src_id, ID(dst) AS dst_id
    """
    with driver.session() as session:
        result = session.run(query)
        return pd.DataFrame([record.data() for record in result])

def update_node_with_embedding(node_id, embedding):
    query = """
    MATCH (n)
    WHERE ID(n) = $node_id
    SET n.embedding = $embedding
    """
    with driver.session() as session:
        session.run(query, node_id=node_id, embedding=embedding)

client = OpenAI()

while True:
    df = fetch_data()
    if df.empty:
        print("No new nodes without embeddings. Terminating script...")
        break
    
    for index, row in df.iterrows():
        text_src = f"{row['protocol']} {row['tcp_flags']} {row['src_ip']} {row['src_port']} {row['src_mac']} {row['dst_ip']} {row['dst_port']} {row['dst_mac']} {row['size']}"
        text_dst = f"{row['protocol']} {row['tcp_flags']} {row['dst_ip']} {row['dst_port']} {row['dst_mac']} {row['src_ip']} {row['src_port']} {row['src_mac']} {row['size']}"
        print(f"Fetching embedding for row {index+1}/{len(df)}, using: {text_src} AND {text_dst}")
        
        response_src = client.embeddings.create(input=text_src, model="text-embedding-3-small")
        embedding_src = response_src.data[0].embedding
        update_node_with_embedding(row['src_id'], embedding_src)

        response_dst = client.embeddings.create(input=text_dst, model="text-embedding-3-small")
        embedding_dst = response_dst.data[0].embedding
        update_node_with_embedding(row['dst_id'], embedding_dst)
    
    print("Finished processing current batch of nodes.")
driver.close()
