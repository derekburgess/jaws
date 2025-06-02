import os
import argparse
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.jaws_config import *
from jaws.jaws_dbms import dbms_connection


def fetch_ip_data(driver, database):
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


def add_traffic(ip_address, port, embedding, org, hostname, location, total_size, driver, database):
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
        traffic.TOTAL_SIZE = $total_size
    MERGE (ip_address)-[:TRAFFIC]->(traffic)
    """
    with driver.session(database=database) as session:
        session.run(query, ip_address=ip_address, port=port, 
                    embedding=embedding, org=org, hostname=hostname, 
                    location=location, total_size=total_size)


class ComputeTransformers:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"\nUsing device: {self.device}")
        self.model = PACKET_MODEL
        self.quantization = QUANTIZATION_CONFIG
        self.tokenizer = AutoTokenizer.from_pretrained(self.model)
        self.infer = AutoModelForCausalLM.from_pretrained(self.model, quantization_config=self.quantization, low_cpu_mem_usage=True)

    def compute_transformer_embedding(self, string):
        inputs = self.tokenizer(string, return_tensors="pt", max_length=512, truncation=True).to(self.device)
        with torch.no_grad():
            outputs = self.infer(**inputs, output_hidden_states=True, use_cache=False)
        last_hidden_states = outputs.hidden_states[-1]
        embeddings = last_hidden_states[:, 0, :].cpu().numpy().tolist()[0]
        return embeddings

    def process_transformer_ip(self):
        df = fetch_ip_data(self.driver, self.database)
        print(f"\nComputing {len(df)} embeddings using {self.model}", "\n")
        for _, row in df.iterrows():
            ip_port_string = f"""
            IP Address: {row['ip_address']}
            Port: {row['port']} ({row['total_size']})
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            """
            embedding = self.compute_transformer_embedding(ip_port_string)
            if embedding is not None:
                add_traffic(row['ip_address'], row['port'], embedding, 
                            row['org'], row['hostname'], row['location'], 
                            row['total_size'], self.driver, self.database)
                print(ip_port_string)

    def transform(self):
        self.process_transformer_ip()
        self.driver.close()


class ComputeOpenAI:
    def __init__(self, client, driver, database):
        self.client = client
        self.driver = driver
        self.database = database
        self.model = OPENAI_EMBEDDING_MODEL

    def compute_openai_embedding(self, input):
        try:
            response = self.client.embeddings.create(input=input, model=self.model)
            return response.data[0].embedding
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def process_openai_ip(self):
        df = fetch_ip_data(self.driver, self.database)
        print(f"\nComputing {len(df)} embeddings using OpenAI {self.model}", "\n")
        for _, row in df.iterrows():
            ip_port_string = f"""
            IP Address: {row['ip_address']}
            Port: {row['port']} ({row['total_size']})
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            """
            embedding = self.compute_openai_embedding(ip_port_string)
            if embedding is not None:
                add_traffic(row['ip_address'], row['port'], embedding, 
                            row['org'], row['hostname'], row['location'], 
                            row['total_size'], self.driver, self.database)
                print(ip_port_string)

    def transform(self):
        self.process_openai_ip()
        self.driver.close()


def main():
    parser = argparse.ArgumentParser(description="Compute organization embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the api to use for computing embeddings, either openai or transformers (default: openai).")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the Neo4j database to connect to (default: '{DATABASE}').")

    args = parser.parse_args()

    try:
        driver = dbms_connection(args.database)
    except Exception as e:
        print(f"\n{str(e)}\n")
        return

    if args.api == "transformers":
        transformer = ComputeTransformers(driver, args.database)
    else:
        transformer = ComputeOpenAI(CLIENT, driver, args.database)

    transformer.transform()

if __name__ == "__main__":
    main()