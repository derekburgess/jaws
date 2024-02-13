from neo4j import GraphDatabase
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModel
from concurrent.futures import ThreadPoolExecutor, as_completed

uri = "bolt://localhost:7687"
username = "neo4j"
password = "testtest"
driver = GraphDatabase.driver(uri, auth=(username, password))

model_name = "bigcode/starcoder"
tokenizer = AutoTokenizer.from_pretrained("bigcode/starcoder", token='KEY')
model = AutoModel.from_pretrained("bigcode/starcoder", token='KEY').to('cuda')

def fetch_data(batch_size=25):
    print("\nFetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE (src.embedding IS NULL) OR (dst.embedding IS NULL)
    RETURN src, dst, p.protocol AS protocol, p.tcp_flags AS tcp_flags, src.address AS src_ip, dst.address AS dst_ip, p.src_port AS src_port, p.dst_port AS dst_port, p.src_mac AS src_mac, p.dst_mac AS dst_mac, p.size AS size, p.payload AS payload, ID(src) AS src_id, ID(dst) AS dst_id
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
    inputs = tokenizer(text, return_tensors="pt", max_length=512, truncation=True).to('cuda')
    with torch.no_grad():
        outputs = model(**inputs)
    embeddings = outputs.last_hidden_state[:, 0, :].cpu().numpy().tolist()[0]
    return embeddings

def process_embeddings(df):
    texts_and_ids = [(f"{row['src_ip']}:{row['src_port']}({row['src_mac']}) > {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) using: {row['protocol']}({row['tcp_flags']}), sending: {row['size']}({row['payload']})", row['src_id']) for _, row in df.iterrows()]
    texts_and_ids += [(f"{row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) > {row['src_ip']}:{row['src_port']}({row['src_mac']}) using: {row['protocol']}({row['tcp_flags']}), sending: {row['size']}({row['payload']})", row['dst_id']) for _, row in df.iterrows()]

    #print("\nStarting parallel processing for embeddings...")
    print("\nProcessing embedding(s)...")
    #example_text, example_id = texts_and_ids[0]
    #print(f"\nExample text for node ID {example_id}: {example_text}") 

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
    #print("\nFinished processing current batch of nodes.")
    print("\nFinished processing embedding(s)...")

driver.close()
print("Closed connection to Neo4j.")