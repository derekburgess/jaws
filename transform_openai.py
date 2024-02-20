from neo4j import GraphDatabase
import pandas as pd
import openai
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data(batch_size=25):
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
    LIMIT $batch_size
    """
    with driver.session() as session:
        result = session.run(query, batch_size=batch_size)
        df = pd.DataFrame([record.data() for record in result])
    print(f"Retrieved {len(df)} records without embeddings.")
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
    client = OpenAI()
    try:
        response = client.embeddings.create(input=text, model="text-embedding-3-large")
        return response.data[0].embedding
    except openai.APIError as e:
        if e.status_code == 400:
            print(f"OpenAI API returned a 400 Error: {e}")
            return "token_string_too_large"
        else:
            print(f"OpenAI API returned an API Error: {e}")
            return None
    except openai.APIConnectionError as e:
        print(f"Failed to connect t OpenAI API: {e}")
        return None
    except openai.RateLimitError as e:
        print(f"OpenAI API request exceeded rate limit: {e}")
        return None

# Note: Sending all of the Payload options to OpenAI will often trigger the token limit error... Have seen packet payloads exceed 15,000 tokens and the OpenAI embeddings end-point has a max 8750. I have included only the binary payload for this example.
def process_embeddings(df):
    texts_and_ids = [(f"{row['src_ip']}:{row['src_port']}({row['src_mac']}) > {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) using: {row['protocol']}({row['tcp']}), sending: [binary: {row['binary']}] at a size of: {row['size']} with ownership: {row['org']}, {row['hostname']}({row['dns']}), {row['location']}", row['packet_id']) for _, row in df.iterrows()]
    
    # Reverse direction
    texts_and_ids += [(f"{row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) > {row['src_ip']}:{row['src_port']}({row['src_mac']}) using: {row['protocol']}({row['tcp']}), sending: [binary: {row['binary']}] at a size of: {row['size']} with ownership: {row['org']}, {row['hostname']}({row['dns']}), {row['location']}", row['packet_id']) for _, row in df.iterrows()]
    
    print("\nStarting parallel processing for embeddings...")
    for text, id in texts_and_ids:
        print(f"\nText for node ID {id}: {text}")

    with ThreadPoolExecutor(max_workers=25) as executor:
        future_to_id = {executor.submit(get_embedding, text): node_id for text, node_id in texts_and_ids}
        for future in as_completed(future_to_id):
            node_id = future_to_id[future]
            try:
                embedding = future.result()
                if embedding is not None:
                    update_neo4j(node_id, embedding)
            except Exception as exc:
                print(f'Node ID {node_id} generated an exception: {exc}')

while True:
    df = fetch_data(25)  # Adjust batch size if needed -- Unlike the StarCoder script, this works much better in conjuction with ThreadPoolExecutor. The current 25/25 settings are from my testing and consume ~60%-80% CPU using a 12th gen i5.
    if df.empty:
        print("No new nodes without embeddings. Terminating script...")
        break
    
    process_embeddings(df)
    print("\nFinished processing current batch of nodes.")

driver.close()
print("Closed connection to Neo4j.")
