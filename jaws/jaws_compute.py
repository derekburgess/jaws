import os
import argparse
import warnings
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig
from openai import OpenAI

uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()

def check_database_exists(uri, username, password, database):
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))
        with driver.session(database=database) as session:
            session.run("RETURN 1")
        return driver
    except ServiceUnavailable:
        raise Exception(f"Unable to connect to Neo4j database. Please check your connection settings.")
    except Exception as e:
        if "database does not exist" in str(e).lower():
            raise Exception(f"{database} database not found. You need to create the default 'captures' database or pass an existing database name.")
        else:
            raise

def fetch_ip_data(database):
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

def add_traffic(ip_address, port, embedding, org, hostname, location, total_size, database):
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

    def process_transformer_ip(self):
        df = fetch_ip_data(self.database)
        print(f"\nComputing {len(df)} embeddings using {self.model_name}", "\n")
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
                            row['total_size'], self.database)
                print(ip_port_string)

    def transform(self):
        self.process_transformer_ip()
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

    def process_openai_ip(self):
        df = fetch_ip_data(self.database)
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
                            row['total_size'], self.database)
                print(ip_port_string)

    def transform(self):
        self.process_openai_ip()
        self.driver.close()

def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    
    parser = argparse.ArgumentParser(description="Compute organization embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the api to use for computing embeddings, either openai or transformers (default: openai).")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures).")

    args = parser.parse_args()

    try:
        check_database_exists(uri, username, password, args.database)
    except Exception as e:
        print(f"\n{str(e)}\n")
        return

    if args.api == "transformers":
        transformer = ComputeTransformers(driver, args.database)
    else:
        transformer = ComputeOpenAI(client, driver, args.database)

    transformer.transform()

if __name__ == "__main__":
    main()