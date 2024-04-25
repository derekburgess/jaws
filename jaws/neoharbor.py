import os
import argparse
from neo4j import GraphDatabase
import pandas as pd
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
        print(f"\nAlong with a detailed system prompt, this program also sends: {df.shape[0]} packets(snapshot below)\n")
        df = df.sample(frac=1)
        print(df.head())
        df_json = df.to_json(orient="records")
        #print(df_json)
        return df_json


def generate_response(df_json):
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

    completion = client.chat.completions.create(
        model="gpt-3.5-turbo-16k",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Snapshot of network traffic: {df_json}"}
        ]
    )

    print(completion.choices[0].message.content)
    #print(completion.usage)

def main():
    parser = argparse.ArgumentParser(description="Request router and firewall advice using OpenAI's GPT-X.")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    print("\nArrgh, this uses OpenAI Chat Completion to analyze and return summarization of network traffic patterns.")
    df_json = fetch_data(driver, args.database)

    input("\nPress Enter to send data and generate a response...")
    generate_response(df_json)

if __name__ == "__main__":
    main()