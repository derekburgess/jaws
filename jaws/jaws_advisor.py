import os
import argparse
import warnings
from neo4j import GraphDatabase
import pandas as pd
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI
import base64
import requests


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()
jaws_finder_endpoint = os.getenv("JAWS_FINDER_ENDPOINT")
image_to_encode = f"{jaws_finder_endpoint}pca_dbscan_outliers.png"
system_prompt = """
As an expert IT Professional, Sysadmin, and Analyst, you are tasked with reviewing logs and reports of networking traffic to identify patterns and suggest improvements for firewall configurations. Please analyze the provided network traffic data and make recommendations for enhancing firewall security, returning a brief report in the following format:

Traffic Analysis:

1. Identify common traffic patterns. Summarize these using an ASCII based diagrammatic notation that includes organizations, hostnames, IP addresses, port numbers, and traffic size (e.g., ( org, hostname ) - [ ownership ] -> ( src_ip:src_port ) - [ packet size ] -> ( dst_ip:dst_port ).

2. Highlight any anomalies or unusual patterns.

Firewall Recommendations:

1. Based on the traffic patterns identified, list detailed recommendations for enhancing firewall security. Each recommendation should refer directly to specific addresses, ports, or domains observed in the dataset.

2. Provide a rationale for each recommendation, explaining why it addresses a specific issue identified in the traffic analysis.

Additional Instructions:

- Begin with an executive summary of the traffic analysis.
- Use clear, concise language.
- Utilize ASCII diagrams to represent traffic flows effectively.
- Ensure recommendations are specific and supported by data from the provided logs.
- Avoid too much formatting.
"""


def fetch_data(driver, database):
    query = """
    MATCH (org:ORGANIZATION)-[:ANOMALY]->(outlier:OUTLIER)
    RETURN org.org AS org,
        org.hostname AS hostname,
        outlier.src_ip AS src_ip,
        outlier.src_port AS src_port,
        outlier.dst_ip AS dst_ip,
        outlier.dst_port AS dst_port,
        outlier.size AS size,
        outlier.protocol AS protocol,
        outlier.location AS location
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
        print(f"\nSending {df.shape[0]} outliers (snapshot below):", "\n")
        print(df.head(), "\n")
        df_json = df.to_json(orient="records")
        return df_json
    

def encode_image(image_path):
  with open(image_path, "rb") as image_file:
    print(f"\nEncoding and sending image from: {image_path}")
    return base64.b64encode(image_file.read()).decode('utf-8')
base64_image = encode_image(image_to_encode)


class SummarizeTransformers:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
        self.model_name = "meta-llama/Meta-Llama-3-8B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForCausalLM.from_pretrained(self.model_name, torch_dtype=torch.bfloat16, device_map="auto")

    def generate_summary_from_transformers(self, system_prompt, df_json):
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Snapshot of network traffic: {df_json}"},
        ]

        input_ids = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            return_tensors="pt"
        ).to(self.model.device)

        outputs = self.model.generate(
            input_ids,
            max_length=8192,
            pad_token_id=self.tokenizer.eos_token_id,
            do_sample=True,
            temperature=0.6,
            top_p=0.9,
        )
        response = outputs[0][input_ids.shape[-1]:]
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
    
    parser = argparse.ArgumentParser(description="Pass data snapshot and return network analsysis using OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai",
                        help="Specify the api to use for network traffic analysis, either openai or transformers (default: openai)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()

    df_json = fetch_data(driver, args.database)

    if args.api == "transformers":
        transformer = SummarizeTransformers()
        transformer.generate_summary_from_transformers(system_prompt, df_json)
    elif args.api == "openai":
        transformer = SummarizeOpenAI(client)
        transformer.generate_summary_from_openai(system_prompt, df_json)
    else:
        print("Invalid API specified. Try openai or transformers.")


if __name__ == "__main__":
    main()