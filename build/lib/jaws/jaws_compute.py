import os
import argparse
import warnings
from neo4j import GraphDatabase
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from openai import OpenAI


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()

#WHERE p.packet_embedding IS NULL as it was causing warnings.
def fetch_packet_data(database):
    query = """
    MATCH (src:SRC_IP)-[p:PACKET]->(dst:DST_IP)
    WITH src, dst, p
    OPTIONAL MATCH (src)-[:OWNERSHIP]->(org:ORGANIZATION)
    RETURN src.src_address AS src_ip,
        src.src_port AS src_port,
        src.src_mac AS src_mac,
        dst.dst_address AS dst_ip,  
        dst.dst_port AS dst_port, 
        dst.dst_mac AS dst_mac,
        p.protocol AS protocol,
        p.size AS size,
        org.org AS org,
        org.hostname AS hostname,
        org.location AS location, 
        elementId(p) AS packet_id
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df

#WHERE org.org_embedding IS NULL as it was causing warnings.
def fetch_org_data(database):
    query = """
    MATCH (src:SRC_IP)-[:OWNERSHIP]->(org:ORGANIZATION)
    WITH org, collect(src) AS src_nodes
    RETURN org.org AS org,
        org.hostname AS hostname,
        org.location AS location,
        [node in src_nodes | node.src_address] AS src_ips,
        [node in src_nodes | node.src_port] AS src_ports,
        [node in src_nodes | node.src_mac] AS src_macs
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def update_packet(packet_id, embedding, database):
    query = """
    MATCH (src:SRC_IP)-[p:PACKET]->(dst:DST_IP)
    WHERE elementId(p) = $packet_id
    SET p.packet_embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, packet_id=packet_id, embedding=embedding)


def update_org(org, embedding, database):
    query = """
    MATCH (org:ORGANIZATION)
    WHERE org.org = $org
    SET org.org_embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, org=org, embedding=embedding)


class ComputeTransformers:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
        self.model_name = "bigcode/starcoder2-3b"
        self.quantization_config = BitsAndBytesConfig(load_in_8bit=True)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.huggingface_token)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, quantization_config=self.quantization_config, token=self.huggingface_token)

    def compute_transformer_embedding(self, packet_string):
        inputs = self.tokenizer(packet_string, return_tensors="pt", max_length=512, truncation=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs, output_hidden_states=True)
        last_hidden_states = outputs.hidden_states[-1]
        embeddings = last_hidden_states[:, 0, :].cpu().numpy().tolist()[0]
        return embeddings

    def process_transformer_packet(self, df):
        print(f"\nComputing {len(df)} packet-embeddings using {self.model_name}", "\n")
        for _, row in df.iterrows():
            packet_string = f"(NODE ORGANIZATION: {row['org']}, hostname: {row['hostname']}, location: {row['location']}) - [OWNERSHIP] -> (NODE SRC_IP: {row['src_ip']}:{row['src_port']}({row['src_mac']})) - [PACKET: protocol: {row['protocol']} size:{row['size']}] -> (NODE DST_IP: {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}))"

            embedding = self.compute_transformer_embedding(packet_string)
            if embedding is not None:
                update_packet(row['packet_id'], embedding, self.database)
                print(packet_string, "\n")

    def process_transformer_org(self):
        df = fetch_org_data(self.database)
        print(f"\nComputing {len(df)} org-embeddings using {self.model_name}", "\n")
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            Source IPs: {', '.join(row['src_ips'])}
            Source Ports: {', '.join(map(str, row['src_ports']))}
            Source MACs: {', '.join(row['src_macs'])}
            """
            embedding = self.compute_transformer_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print(org_string,)

    def transform(self, transform_type):
        if transform_type == 'packet':
            df = fetch_packet_data(self.database)
            if not df.empty:
                self.process_transformer_packet(df)
            else:
                print("No entities left to compute")
        elif transform_type == 'org':
            self.process_transformer_org()

        self.driver.close()


class ComputeOpenAI:
    def __init__(self, client, driver, database):
        self.client = client
        self.driver = driver
        self.database = database
        self.model = "text-embedding-3-large"

    def compute_openai_embedding(self, text):
        try:
            response = self.client.embeddings.create(input=text, model=self.model)
            return response.data[0].embedding
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def process_openai_packet(self, df):
        print(f"\nComputing {len(df)} packet-embeddings using OpenAI {self.model}", "\n")
        for _, row in df.iterrows():
            packet_string = f"(NODE ORGANIZATION: {row['org']}, hostname: {row['hostname']}, location: {row['location']}) - [OWNERSHIP] -> (NODE SRC_IP: {row['src_ip']}:{row['src_port']}({row['src_mac']})) - [PACKET: protocol: {row['protocol']} size:{row['size']}] -> (NODE DST_IP: {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}))"

            embedding = self.compute_openai_embedding(packet_string)
            if embedding is not None:
                update_packet(row['packet_id'], embedding, self.database)
                print(packet_string, "\n")

    def process_openai_org(self):
        df = fetch_org_data(self.database)
        print(f"\nComputing {len(df)} org-embeddings using OpenAI {self.model}", "\n")
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            Source IPs: {', '.join(row['src_ips'])}
            Source Ports: {', '.join(map(str, row['src_ports']))}
            Source MACs: {', '.join(row['src_macs'])}
            """
            embedding = self.compute_openai_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print(org_string, "\n")

    def transform(self, transform_type):
        if transform_type == 'packet':
            df = fetch_packet_data(self.database)
            if not df.empty:
                self.process_openai_packet(df)
            else:
                print("No entities left to compute")
        elif transform_type == 'org':
            self.process_openai_org()

        self.driver.close()


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    
    parser = argparse.ArgumentParser(description="Process embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai",
                        help="Specify the api to use for computing embeddings, either openai or transformers (default: openai)")
    parser.add_argument("--type", choices=["packet", "org"], default="packet",
                        help="Specify the embedding string type to pass (default: packet)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()

    if args.api == "transformers":
        transformer = ComputeTransformers(driver, args.database)
    elif args.api == "openai":
        transformer = ComputeOpenAI(client, driver, args.database)
    else:
        print("Invalid API specified. Try openai or transformers.")
        exit(1)

    transformer.transform(args.type)

if __name__ == "__main__":
    main()
