from neo4j import GraphDatabase
import pandas as pd
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

uri = "bolt://localhost:7687"
username = "neo4j"
password = "testtest"
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data(batch_size=25):
    print("Fetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE (src.embedding IS NULL) OR (dst.embedding IS NULL)
    RETURN src, dst, p.protocol AS protocol, p.tcp_flags AS tcp_flags, src.address AS src_ip, dst.address AS dst_ip, p.src_port AS src_port, p.dst_port AS dst_port, p.src_mac AS src_mac, p.dst_mac AS dst_mac, p.size AS size, ID(src) AS src_id, ID(dst) AS dst_id
    LIMIT $batch_size
    """
    with driver.session() as session:
        result = session.run(query, batch_size=batch_size)
        df = pd.DataFrame([record.data() for record in result])
    print(f"Retrieved {len(df)} records.")
    return df

def update_node_with_embedding(node_id, embedding):
    query = """
    MATCH (n)
    WHERE ID(n) = $node_id
    SET n.embedding = $embedding
    """
    with driver.session() as session:
        session.run(query, node_id=node_id, embedding=embedding)

def get_embedding(text):
    client = OpenAI()
    response = client.embeddings.create(input=text, model="text-embedding-3-small")
    return response.data[0].embedding

def process_embeddings(df):
    texts_and_ids = [(f"{row['protocol']} {row['tcp_flags']} {row['src_ip']} {row['src_port']} {row['src_mac']} {row['dst_ip']} {row['dst_port']} {row['dst_mac']} {row['size']}", row['src_id']) for _, row in df.iterrows()]
    texts_and_ids += [(f"{row['protocol']} {row['tcp_flags']} {row['dst_ip']} {row['dst_port']} {row['dst_mac']} {row['src_ip']} {row['src_port']} {row['src_mac']} {row['size']}", row['dst_id']) for _, row in df.iterrows()]

    print("Starting parallel processing for embeddings...")
    with ThreadPoolExecutor(max_workers=25) as executor:
        future_to_id = {executor.submit(get_embedding, text): node_id for text, node_id in texts_and_ids}
        for future in as_completed(future_to_id):
            node_id = future_to_id[future]
            try:
                embedding = future.result()
            except Exception as exc:
                print(f'Node ID {node_id} generated an exception: {exc}')
            else:
                update_node_with_embedding(node_id, embedding)

while True:
    df = fetch_data(25)  # Adjust batch size if needed
    if df.empty:
        print("No new nodes without embeddings. Terminating script...")
        break
    
    process_embeddings(df)
    print("Finished processing current batch of nodes.")

driver.close()
print("Closed connection to Neo4j.")
