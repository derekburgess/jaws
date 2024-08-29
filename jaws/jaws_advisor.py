import os
import argparse
import warnings
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
import pandas as pd
import numpy as np
import torch
import transformers
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI
import base64


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()
jaws_finder_endpoint = os.getenv("JAWS_FINDER_ENDPOINT")
image_to_encode = f"{jaws_finder_endpoint}/pca_dbscan_outliers.png"


system_prompt = """
You are an expert IT Professional, Sysadmin, and Analyst. Your task is to review data from network traffic to identify patterns and make recommendations for firewall configurations. Please analyze the provided network traffic and cluster plot, then return a brief report in the following format:

---

Executive Summary:

A concise summary of the traffic analysis, including a description of the cluster plot.

Traffic Analysis:

1. Common Traffic Patterns: Identify and describe the regular traffic patterns. Highlight any anomalies or unusual patterns.
   
2. Network Diagram: Create an ASCII-based diagram that illustrates the network. Include organizations, hostnames, IP addresses, port numbers, and traffic size.

Firewall Recommendations:

1. Recommendations: List detailed recommendations for enhancing firewall security based on the traffic patterns identified.
   
2. Rationale: Provide a rationale for each recommendation, explaining how it addresses specific issues identified in the traffic analysis.

Additional Instructions:

- Use clear, concise language.
- Avoid markdown formatting, this is a CLI tool.
- Utilize ASCII diagrams to represent traffic flows effectively.
- Ensure recommendations are specific and supported by data from the provided logs.
- Avoid excessive formatting.
"""


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


def fetch_data(driver, database):
    query = """
    MATCH (org:ORGANIZATION)
    WHERE org.is_anomaly = true
    MATCH (ip:IP)-[:OWNERSHIP]->(org)
    MATCH (ip)-[p:PACKET]->(dst:IP)
    RETURN org.org AS org,
        org.hostname AS hostname,
        ip.address AS src_ip,
        p.src_port AS src_port,
        dst.address AS dst_ip,
        p.dst_port AS dst_port,
        p.size AS size,
        p.protocol AS protocol,
        org.location AS location
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        data = []
        for record in result:
            data.append({
                'org': record['org'],
                'hostname': record['hostname'],
                'src_ip': record['src_ip'],
                'src_port': record['src_port'],
                'dst_ip': record['dst_ip'],
                'dst_port': record['dst_port'],
                'size': record['size'],
                'protocol': record['protocol'],
                'location': record['location']
            })
        df = pd.DataFrame(data)
        df_json = df.to_json(orient="records")
        return df, df_json


def encode_image(image_path):
  with open(image_path, "rb") as image_file:
    return base64.b64encode(image_file.read()).decode('utf-8')
base64_image = encode_image(image_to_encode)


class SummarizeTransformers:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"\nUsing device: {self.device}")
        self.huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
        self.model_name = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, token=self.huggingface_token)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, token=self.huggingface_token, torch_dtype=torch.bfloat16).to(self.device)

    def generate_summary_from_transformers(self, system_prompt, df_json):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Snapshot of network traffic: {df_json}"},
        ]
        
        inputs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt"
        ).to(self.device)
        
        outputs = self.model.generate(
            inputs,
            max_length=8192,
            pad_token_id=self.tokenizer.eos_token_id,
            #do_sample=True,
            #temperature=0.6,
            #top_p=0.9,
        )
        response = outputs[0][inputs.shape[-1]:]
        print(f"\nAnalysis from {self.model_name}:", "\n")
        print(self.tokenizer.decode(response, skip_special_tokens=True), "\n")


class SummarizeOpenAI:
    def __init__(self, client):
        self.client = client
        self.model_name = "gpt-4o"
    
    def generate_summary_from_openai(self, system_prompt, df_json):
        completion = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",
                 "content": [
                    {
                        "type": "text",
                        "text": f"Snapshot of network traffic: {df_json}"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                            "detail": "high"
                        }
                    }
                ]
            }  
        ]      
        )
        print(f"\nAnalysis from {self.model_name}", "\n")
        print(completion.choices[0].message.content, "\n")


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    
    parser = argparse.ArgumentParser(description="Pass data snapshot and return network analysis using OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the api to use for network traffic analysis, either openai or transformers (default: openai).")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures).")

    args = parser.parse_args()

    try:
        check_database_exists(uri, username, password, args.database)
    except Exception as e:
        print(f"\n{str(e)}\n")
        return

    df, df_json = fetch_data(driver, args.database)

    if args.api == "transformers":
        transformer = SummarizeTransformers()
        print(f"\nSending {df.shape[0]} packets to {transformer.model_name} (snapshot below):", "\n")
        print(df.head(), "\n")
        transformer.generate_summary_from_transformers(system_prompt, df_json)
    else:
        openai = SummarizeOpenAI(client)
        print(f"\nEncoding and sending image from: {image_to_encode}")
        print(f"\nSending {df.shape[0]} packets to {openai.model_name} (snapshot below):", "\n")
        print(df.head(), "\n")
        openai.generate_summary_from_openai(system_prompt, df_json)

if __name__ == "__main__":
    main()