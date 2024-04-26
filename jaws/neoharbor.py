import os
import argparse
from neo4j import GraphDatabase
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from openai import OpenAI


uri = os.getenv("LOCAL_NEO4J_URI")
username = os.getenv("LOCAL_NEO4J_USERNAME")
password = os.getenv("LOCAL_NEO4J_PASSWORD")
driver = GraphDatabase.driver(uri, auth=(username, password))
client = OpenAI()


def fetch_data(driver, database):
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE p.embedding IS NOT NULL
    OPTIONAL MATCH (src)-[:OWNERSHIP]->(org:ORGANIZATION)
    RETURN src.address AS src_ip, 
        src.src_port AS src_port,
        dst.address AS dst_ip,  
        dst.dst_port AS dst_port,
        p.protocol AS protocol, 
        p.size AS size,
        org.name AS org,
        org.hostname AS hostname
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        data = []
        for record in result:
            data.append({
                'src_ip': record['src_ip'],
                'src_port': record['src_port'],
                'dst_ip': record['dst_ip'],
                'dst_port': record['dst_port'],
                'protocol': record['protocol'],
                'size': record['size'],
                'org': record['org'],
                'hostname': record['hostname'],
            })
        df = pd.DataFrame(data)
        print(f"\nPassing: {df.shape[0]} packets (snapshot below)\n")
        df = df.sample(frac=1)
        print(df.head(), "\n")
        df_json = df.to_json(orient="records")
        return df_json
  

class SummarizeLlama:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.huggingface_token = os.getenv("HUGGINGFACE_KEY")
        self.model_name = "meta-llama/Meta-Llama-3-8B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_name,
            torch_dtype=torch.bfloat16,
            device_map="auto"
        )

    def generate_summary_from_llama(self, df_json):
        system_prompt = """
        As an expert IT Professional, Sysadmin, and Analyst, you are tasked with reviewing logs of networking traffic to identify patterns and suggest improvements for firewall configurations. Your analysis should focus on:

        Traffic Analysis:
        1. Identify common traffic patterns. Summarize these using a diagrammatic notation that includes organizations, hostnames, IP addresses, port numbers, and traffic size (e.g., [org] [hostname] (src_ip:src_port) - size -> (dst_ip:dst_port)).
        
        2. Highlight any anomalies or unusual patterns.

        Firewall Recommendations:

        1. Based on the traffic patterns identified, list detailed recommendations for enhancing firewall security. Each recommendation should refer directly to specific addresses, ports, or domains observed in the dataset.

        2. Provide a rationale for each recommendation, explaining why it addresses a specific issue identified in the traffic analysis.

        Instructions:

        - Use clear, concise language.
        - Utilize diagrams to represent traffic flows effectively.
        - Ensure recommendations are specific and supported by data from the provided logs.
        """

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
            max_length=6400,
            temperature=0.6,
            top_p=0.9,
        )
        response = outputs[0][input_ids.shape[-1]:]
        print(self.tokenizer.decode(response, skip_special_tokens=True))


class SummarizeOpenAI:
    def __init__(self, client):
        self.client = client

    def generate_summary_from_openai(self, df_json):
        system_prompt = """
        As an expert IT Professional, Sysadmin, and Analyst, you are tasked with reviewing logs of networking traffic to identify patterns and suggest improvements for firewall configurations. Your analysis should focus on:

        Traffic Analysis:
        1. Identify common traffic patterns. Summarize these using a diagrammatic notation that includes organizations, hostnames, IP addresses, port numbers, and traffic size (e.g., [org] [hostname] (src_ip:src_port) - size -> (dst_ip:dst_port)).
        
        2. Highlight any anomalies or unusual patterns.

        Firewall Recommendations:

        1. Based on the traffic patterns identified, list detailed recommendations for enhancing firewall security. Each recommendation should refer directly to specific addresses, ports, or domains observed in the dataset.

        2. Provide a rationale for each recommendation, explaining why it addresses a specific issue identified in the traffic analysis.

        Instructions:

        - Use clear, concise language.
        - Utilize diagrams to represent traffic flows effectively.
        - Ensure recommendations are specific and supported by data from the provided logs.
        """

        completion = self.client.chat.completions.create(
            model="gpt-3.5-turbo-16k",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Snapshot of network traffic: {df_json}"}
            ]
        )

        print(completion.choices[0].message.content)


def main():
    parser = argparse.ArgumentParser(description="Pass data snapshot and return network analsysis using OpenAI or Meta Llama-3 8B Instruct")
    parser.add_argument("--api", choices=["openai", "llama"], default="openai",
                        help="Specify the api to use for network traffic analysis (default: openai)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    df_json = fetch_data(driver, args.database)

    if args.api == "llama":
        transformer = SummarizeLlama()
        transformer.generate_summary_from_llama(df_json)
    elif args.api == "openai":
        transformer = SummarizeOpenAI(client)
        transformer.generate_summary_from_openai(df_json)
    else:
        print("Invalid API specified. Try openai or llama.")


if __name__ == "__main__":
    main()