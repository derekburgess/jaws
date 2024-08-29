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


def fetch_org_data(database):
    query = """
    MATCH (ip:IP)-[:OWNERSHIP]->(org:ORGANIZATION)
    WITH org, collect(ip) AS ip_nodes
    RETURN org.org AS org,
        org.hostname AS hostname,
        org.location AS location,
        [node in ip_nodes | node.address] AS ip_addresses
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


def update_org(org, embedding, database):
    query = """
    MATCH (org:ORGANIZATION {org: $org})
    SET org.org_embedding = $embedding
    """
    with driver.session(database=database) as session:
        session.run(query, org=org, embedding=embedding)


class ComputeTransformers:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"\nUsing device: {self.device}")
        self.huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
        self.model_name = "bigcode/starcoder2-3b"
        self.quantization_config = BitsAndBytesConfig(load_in_8bit=True)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.huggingface_token)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, quantization_config=self.quantization_config, token=self.huggingface_token, low_cpu_mem_usage=True)

    def compute_transformer_embedding(self, string):
        inputs = self.tokenizer(string, return_tensors="pt", max_length=512, truncation=True).to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs, output_hidden_states=True, use_cache=False)
        last_hidden_states = outputs.hidden_states[-1]
        embeddings = last_hidden_states[:, 0, :].cpu().numpy().tolist()[0]
        return embeddings

    def process_transformer_org(self):
        df = fetch_org_data(self.database)
        print(f"\nComputing {len(df)} org-embeddings using {self.model_name}", "\n")
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            IP Addresses: {', '.join(row['ip_addresses'])}
            """
            embedding = self.compute_transformer_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print(org_string)

    def transform(self):
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

    def process_openai_org(self):
        df = fetch_org_data(self.database)
        print(f"\nComputing {len(df)} org-embeddings using OpenAI {self.model}", "\n")
        for _, row in df.iterrows():
            org_string = f"""
            Organization: {row['org']}
            Hostname: {row['hostname']}
            Location: {row['location']}
            IP Addresses: {', '.join(row['ip_addresses'])}
            """
            embedding = self.compute_openai_embedding(org_string)
            if embedding is not None:
                update_org(row['org'], embedding, self.database)
                print(org_string)

    def transform(self):
        self.process_openai_org()
        self.driver.close()


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    
    parser = argparse.ArgumentParser(description="Compute organization embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the api to use for computing embeddings, either openai or transformers (default: openai).")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures).")

    args = parser.parse_args()

    if args.api == "transformers":
        transformer = ComputeTransformers(driver, args.database)
    elif args.api == "openai":
        transformer = ComputeOpenAI(client, driver, args.database)
    else:
        print("Invalid API specified. Try openai or transformers.")
        exit(1)

    transformer.transform()

if __name__ == "__main__":
    main()