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


def fetch_data(database):
    #print("\nFetching packet from Neo4j...")
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
        p.payload_binary AS binary,
        p.http_url AS http, 
        p.dns_domain AS dns,
        org.name AS org,
        org.hostname AS hostname,
        org.location AS location, 
        ID(p) AS packet_id
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def update_neo4j(packet_id, embedding, database):
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE ID(p) = $packet_id
    SET p.embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, packet_id=packet_id, embedding=embedding)


class TransformStarCoder:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.huggingface_token = os.getenv("HUGGINGFACE_KEY")
        self.model_name = "bigcode/starcoder2-15b" # bigcode/starcoder for starcoder 1
        self.quantization_config = BitsAndBytesConfig(load_in_8bit=True)  # to use 4bit use `load_in_4bit=True` instead
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.huggingface_token)
        # Remove quantization_config=self.quantization_config if not using quantization or using StarCoder 1
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
            packet_string = f" <<<< FROM: {row['src_ip']}:{row['src_port']}({row['src_mac']}) TO: {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) >>>> using protocols: {row['protocol']}({row['tcp']}), carriring a size of: {row['size']}, and from ownership: {row['org']}, {row['hostname']}({row['dns']}), and location: {row['location']}"
            
            embedding = self.compute_starcoder_embedding(packet_string)
            if embedding is not None:
                update_neo4j(row['packet_id'], embedding, self.database)
                print("\nCompleted embedding(StarCoder2-15b w/ 8bit quantization):")
                print(f"{packet_string}")

    def transform(self):
        while True:
            df = fetch_data(self.database)
            if df.empty:
                print("\nAll packets embedded. Terminating script.")
                break
            
            self.process_starcoder_packet(df)

        self.driver.close()
        print("Closed connection to Neo4j.")


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
            packet_string = f" <<<< {row['src_ip']}:{row['src_port']}({row['src_mac']}) {row['dst_ip']}:{row['dst_port']}({row['dst_mac']}) >>>> using: {row['protocol']}({row['tcp']}), with a size of: {row['size']}, and with ownership: {row['org']}, {row['hostname']}({row['dns']}), and location: {row['location']}"

            embedding = self.compute_openai_embedding(packet_string)
            if embedding is not None:
                update_neo4j(row['packet_id'], embedding, self.database)
                print("\nCompleted embedding(OpenAI text-embedding-3-large):")
                print(f"{packet_string}")

    def transform(self):
        while True:
            df = fetch_data(self.database)
            if df.empty:
                print("\nAll packets embedded. Terminating script.")
                break

            self.process_openai_packet(df)

        self.driver.close()
        print("Closed connection to Neo4j.")


def main():
    parser = argparse.ArgumentParser(description="Process embeddings using either OpenAI or StarCoder2 w/ Quantization.")
    parser.add_argument("--api", choices=["openai", "starcoder"], default="starcoder",
                        help="Specify the api to use for embedding processing (default: starcoder)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()

    if args.api == "starcoder":
        transformer = TransformStarCoder(driver, args.database)
    elif args.api == "openai":
        transformer = TransformOpenAI(client, driver, args.database)
    else:
        print("Invalid api specified.")
        exit(1)

    transformer.transform()

if __name__ == "__main__":
    main()