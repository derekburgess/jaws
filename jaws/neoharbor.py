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
                'size': record['size'],
                'org': record['org'],
                'hostname': record['hostname'],
            })
        df = pd.DataFrame(data)
        df = df.sample(frac=1)
        print(df.head())
        df_json = df.to_json(orient="records")
        #print(df_json)
        return df_json


def generate_response(df_json):
    completion = client.chat.completions.create(
      model="gpt-3.5-turbo-16k",
      messages=[
        {"role": "system", "content": "You are an expert IT Professional, Sysadmin, and Analyst. You review logs of networking traffic looking to identify patterns and improve on firewall security. Im am going to share snapshots of networking traffic and I want you to: 1. Describe the traffic in detail, refering directly to addresses, ports, and domains found in the traffic. 2. Return detail recommendations on how to improve firewall performance. Please list specific addresses, port numbers, or domains when returning recommendations."},
        {"role": "user", "content": f"Here is a snapshot of current network data: {df_json}"}
      ]
    )
    print(completion.choices[0].message.content)
    print(completion.usage)


def main():
    parser = argparse.ArgumentParser(description="Request router and firewall advice using OpenAI's GPT-X.")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    df_json = fetch_data(driver, args.database)
    print("\nAsking the wizard...", "\n")
    generate_response(df_json)

if __name__ == "__main__":
    main()