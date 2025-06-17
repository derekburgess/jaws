import argparse
from rich.live import Live
from rich.console import Group
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from jaws.jaws_config import *
from jaws.jaws_utils import (
    dbms_connection,
    render_error_panel,
    render_info_panel,
    render_success_panel,
    render_activity_panel
)


def fetch_data_for_embedding(driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS)<-[:OWNERSHIP]-(org:ORGANIZATION)
    OPTIONAL MATCH (ip_address)-[:PORT]->(port:PORT)
    OPTIONAL MATCH (port)-[p:PACKET]->()
    WITH ip_address, port, org, sum(p.SIZE) AS total_size
    RETURN ip_address.IP_ADDRESS AS ip_address,
           port.PORT AS port,
           org.ORGANIZATION AS org,
           org.HOSTNAME AS hostname,
           org.LOCATION AS location,
           total_size
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def add_traffic_to_database(ip_address, port, embedding, org, hostname, location, total_size, driver, database):
    query = """
    MATCH (ip_address:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (traffic:TRAFFIC {
        IP_ADDRESS: $ip_address,
        PORT: $port
    })
    SET traffic.EMBEDDING = $embedding,
        traffic.ORGANIZATION = $org,
        traffic.HOSTNAME = $hostname,
        traffic.LOCATION = $location,
        traffic.TOTAL_SIZE = $total_size,
        traffic.TIMESTAMP = datetime()
    MERGE (ip_address)-[:TRAFFIC]->(traffic)
    """
    with driver.session(database=database) as session:
        session.run(query, ip_address=ip_address, port=port, 
                    embedding=embedding, org=org, hostname=hostname, 
                    location=location, total_size=total_size)


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
def compute_transformer_embedding(input):
    tokenizer = AutoTokenizer.from_pretrained(PACKET_MODEL)
    infer = AutoModelForCausalLM.from_pretrained(PACKET_MODEL) # quantization_config=BitsAndBytesConfig(load_in_8bit=True)
    inputs = tokenizer(input, return_tensors="pt", max_length=512, truncation=True).to(device)
    with torch.no_grad():
        outputs = infer(**inputs, output_hidden_states=True, use_cache=False)
    last_hidden_states = outputs.hidden_states[-1]
    embeddings = last_hidden_states[:, 0, :].cpu().numpy().tolist()[0]
    return embeddings


def compute_openai_embedding(client, input):
    response = client.embeddings.create(input=input, model=OPENAI_EMBEDDING_MODEL)
    return response.data[0].embedding


def main():
    parser = argparse.ArgumentParser(description="Compute organization embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the API to use for computing embeddings, either 'openai' or 'transformers' (default: 'openai').")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--agent", action="store_true", help="Disable rich output for agent use.")
    args = parser.parse_args()
    driver = dbms_connection(args.database)
    if driver is None:
        return
    
    df = fetch_data_for_embedding(driver, args.database)
    embedding_strings = []
    embedding_tensors = []
    try:
        if not args.agent:
            processing_message = f"Processing {len(df)} packet embeddings using: {PACKET_MODEL +f"({device})" if args.api == 'transformers' else OPENAI_EMBEDDING_MODEL}"
            with Live(Group(
                    render_info_panel("CONFIG", processing_message, CONSOLE),
                    render_activity_panel("EMBEDDINGS(STR)", embedding_strings, CONSOLE),
                    render_activity_panel("EMBEDDINGS(TENSOR)", [str(tensor) for tensor in embedding_tensors], CONSOLE)
                ), console=CONSOLE, refresh_per_second=10) as live:
                
                for _, row in df.iterrows():
                    embedding_string = f"IP Address: {row['ip_address']} | Port: {row['port']} ({row['total_size']})\nOrganization: {row['org']} | Hostname: {row['hostname']} | Location: {row['location']}\n"
                    if args.api == "transformers":
                        embedding = compute_transformer_embedding(embedding_string)
                    else:
                        embedding = compute_openai_embedding(CLIENT, embedding_string)
                        
                    if embedding is not None:
                        add_traffic_to_database(row['ip_address'], row['port'], embedding, 
                                    row['org'], row['hostname'], row['location'], 
                                    row['total_size'], driver, args.database)
                        embedding_strings.append(embedding_string)
                        embedding_tensors.append(embedding)
                        live.update(Group(
                            render_info_panel("CONFIG", processing_message, CONSOLE),
                            render_activity_panel("EMBEDDINGS(STR)", embedding_strings, CONSOLE),
                            render_activity_panel("EMBEDDINGS(TENSOR)", [str(tensor) for tensor in embedding_tensors], CONSOLE)
                        ))

                live.stop()
                CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Embeddings({len(embedding_strings)}) added to: '{args.database}'", CONSOLE))
        else:
            for _, row in df.iterrows():
                embedding_string = f"IP Address: {row['ip_address']} | Port: {row['port']} ({row['total_size']})\nOrganization: {row['org']} | Hostname: {row['hostname']} | Location: {row['location']}\n"
                if args.api == "transformers":
                    embedding = compute_transformer_embedding(embedding_string)
                else:
                    embedding = compute_openai_embedding(CLIENT, embedding_string)
                    
                if embedding is not None:
                    add_traffic_to_database(row['ip_address'], row['port'], embedding, 
                                row['org'], row['hostname'], row['location'], 
                                row['total_size'], driver, args.database)
                    embedding_strings.append(embedding_string)
                    embedding_tensors.append(embedding)
            print(f"[PROCESS COMPLETE] Embeddings({len(embedding_strings)}) added to: '{args.database}'")
        return

    except Exception as e:
        if not args.agent:
            CONSOLE.print(render_error_panel("ERROR", f"{str(e)}", CONSOLE))
        else:
            return f"\n[ERROR] {str(e)}\n"
        
    finally:
        driver.close()

if __name__ == "__main__":
    main()