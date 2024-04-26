import os
import argparse
from neo4j import GraphDatabase
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from openai import OpenAI


uri = os.getenv("LOCAL_NEO4J_URI")
username = os.getenv("LOCAL_NEO4J_USERNAME")
password = os.getenv("LOCAL_NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()


def fetch_packet_data(database):
    query = """
    MATCH (src:SRC_IP)-[p:PACKET]->(dst:DST_IP)
    WHERE p.packet_embedding IS NULL
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
        ID(p) AS packet_id
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def update_packet(packet_id, embedding, database):
    query = """
    MATCH (src:SRC_IP)-[p:PACKET]->(dst:DST_IP)
    WHERE ID(p) = $packet_id
    SET p.packet_embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, packet_id=packet_id, embedding=embedding)


def fetch_org_data(database):
    query = """
    MATCH (src:SRC_IP)-[:OWNERSHIP]->(org:ORGANIZATION)
    WHERE org.org_embedding IS NULL
    WITH org, collect(src.src_address) AS src_ips
    RETURN org.org AS org,
        org.hostname AS hostname,
        org.location AS location,
        src_ips
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def update_org(org, embedding, database):
    query = """
    MATCH (org:ORGANIZATION)
    WHERE org.org = $org
    SET org.org_embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, org=org, embedding=embedding)


class TransformStarCoder:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.huggingface_token = os.getenv("HUGGINGFACE_KEY")
        self.model_name = "bigcode/starcoder2-15b"
        self.quantization_config = BitsAndBytesConfig(load_in_8bit=True)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.huggingface_token)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, quantization_config=self.quantization_config, token=self.huggingface_token)

    def compute_starcoder_embedding(self, packet_string):
        inputs = self.tokenizer(packet_string, return_tensors="pt", max_length=512, truncation=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs, output_hidden_states=True)
        last_hidden_states = outputs.hidden_states[-1]
        embeddings = last_hidden_states[:, 0, :].cpu().numpy().tolist()[0]
        return embeddings

    def process_starcoder_packet(self, df):
        for _, row in df.iterrows():
            packet_string = f" <<<< FROM: {row['src_ip']}:{row['src_port']}({row['src_mac']}) TO: {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) >>>> using procotol: {row['protocol']}, with a size of: {row['size']}, and with ownership: {row['org']}, {row['hostname']}, and location: {row['location']}"

            embedding = self.compute_starcoder_embedding(packet_string)
            if embedding is not None:
                update_packet(row['packet_id'], embedding, self.database)
                print("Computed packet embedding(StarCoder2-15b-quantization)")
                print(packet_string, "\n")

        if df.empty:
            print("All packets embedded.(StarCoder2-15b-quantization)")

    def process_starcoder_org(self):
        df = fetch_org_data(self.database)
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            Source IPs: {', '.join(row['src_ips'])}
            """
            embedding = self.compute_starcoder_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print("Computed org embedding(StarCoder2-15b-quantization)")
                print(org_string, "\n")

    def transform(self, transform_type):
        if transform_type == 'packets':
            df = fetch_packet_data(self.database)
            if not df.empty:
                self.process_starcoder_packet(df)
            else:
                print("No packets to process.")
        elif transform_type == 'orgs':
            self.process_starcoder_org()

        self.driver.close()


class TransformOpenAI:
    def __init__(self, client, driver, database):
        self.client = client
        self.driver = driver
        self.database = database

    def compute_openai_embedding(self, text):
        try:
            response = self.client.embeddings.create(input=text, model="text-embedding-3-large")
            return response.data[0].embedding
        except Exception as e:
            print(f"An error occurred: {e}")
            return None

    def process_openai_packet(self, df):
        for _, row in df.iterrows():
            packet_string = f" <<<< {row['src_ip']}:{row['src_port']}({row['src_mac']}) {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) >>>> using procotol: {row['protocol']}, with a size of: {row['size']}, and with ownership: {row['org']}, {row['hostname']}, and location: {row['location']}"

            embedding = self.compute_openai_embedding(packet_string)
            if embedding is not None:
                update_packet(row['packet_id'], embedding, self.database)
                print("Computed packet embedding(OpenAI text-embedding-3-large)")
                print(packet_string, "\n")

        if df.empty:
            print("All packets embedded(OpenAI text-embedding-3-large)")

    def process_openai_org(self):
        df = fetch_org_data(self.database)
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            Source IPs: {', '.join(row['src_ips'])}
            """
            embedding = self.compute_openai_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print("Computed org embedding(OpenAI text-embedding-3-large)")
                print(org_string, "\n")

    def transform(self, transform_type):
        if transform_type == 'packets':
            df = fetch_packet_data(self.database)
            if not df.empty:
                self.process_openai_packet(df)
            else:
                print("No packets to process.")
        elif transform_type == 'orgs':
            self.process_openai_org()

        self.driver.close()

def main():
    parser = argparse.ArgumentParser(description="Process embeddings using either OpenAI or StarCoder2 w/ Quantization.")
    parser.add_argument("--api", choices=["openai", "starcoder"], default="starcoder",
                        help="Specify the api to use for embedding processing (default: starcoder)")
    parser.add_argument("--type", choices=["packets", "orgs"], default="packets",
                        help="Specify the packet string type to pass (default: packets)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()

    if args.api == "starcoder":
        transformer = TransformStarCoder(driver, args.database)
    elif args.api == "openai":
        transformer = TransformOpenAI(client, driver, args.database)
    else:
        print("Invalid API specified. Try openai or starcoder.")
        exit(1)

    transformer.transform(args.type)

if __name__ == "__main__":
    main()
