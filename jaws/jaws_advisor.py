import argparse
import base64
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.jaws_config import *
from jaws.jaws_dbms import dbms_connection


def fetch_data(driver, database):
    query = """
    MATCH (traffic:TRAFFIC)
    RETURN DISTINCT
        traffic.IP_ADDRESS AS ip_address,
        traffic.PORT AS port,
        traffic.ORGANIZATION AS org,
        traffic.HOSTNAME AS hostname,
        traffic.LOCATION AS location,
        traffic.TOTAL_SIZE AS total_size,
        traffic.OUTLIER AS outlier
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        data = []
        for record in result:
            data.append({
                'ip_address': record['ip_address'],
                'port': record['port'],
                'org': record['org'],
                'hostname': record['hostname'],
                'location': record['location'],
                'total_size': record['total_size'],
                'outlier': record['outlier']
            })
        df = pd.DataFrame(data)
        df_json = df.to_json(orient="records")
        return df, df_json


def encode_image(image_path):
  with open(image_path, "rb") as image_file:
    return base64.b64encode(image_file.read()).decode('utf-8')
  
EVIDANCE = f"{FINDER_ENDPOINT}/pca_dbscan_outliers.png"
base64_image = encode_image(EVIDANCE)


class SummarizeTransformers:
    def __init__(self):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(f"\nUsing device: {self.device}")
        self.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        self.tokenizer = AutoTokenizer.from_pretrained(self.model)
        self.infer = AutoModelForCausalLM.from_pretrained(self.model, torch_dtype=torch.bfloat16).to(self.device)

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
        
        outputs = self.infer.generate(
            inputs,
            max_length=8192,
            pad_token_id=self.tokenizer.eos_token_id,
            #do_sample=True,
            #temperature=0.6,
            #top_p=0.9,
        )
        response = outputs[0][inputs.shape[-1]:]
        print(f"\nAnalysis from {self.model}:", "\n")
        print(self.tokenizer.decode(response, skip_special_tokens=True), "\n")


class SummarizeOpenAI:
    def __init__(self, client):
        self.client = client
        self.model = OPENAI_LANG_MODEL
    
    def generate_summary_from_openai(self, system_prompt, df_json):
        completion = self.client.chat.completions.create(
            model=self.model,
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
        print(f"\nAnalysis from {self.model}", "\n")
        print(completion.choices[0].message.content, "\n")


def main():
    
    parser = argparse.ArgumentParser(description="Pass data snapshot and return network analysis using OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the api to use for network traffic analysis, either openai or transformers (default: openai).")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the Neo4j database to connect to (default: '{DATABASE}').")

    args = parser.parse_args()

    try:
        driver = dbms_connection(args.database)
    except Exception as e:
        print(f"\n{str(e)}\n")
        return

    df, df_json = fetch_data(driver, args.database)

    if args.api == "transformers":
        transformer = SummarizeTransformers()
        print(f"\nSending {df.shape[0]} all unique addresses to {transformer.model} (snapshot below):", "\n")
        print(df.to_string(index=False), "\n")
        transformer.generate_summary_from_transformers(ADVISOR_SYSTEM_PROMPT, df_json)
    else:
        openai = SummarizeOpenAI(CLIENT)
        print(f"\nEncoding and sending image from: {EVIDANCE}")
        print(f"\nSending {df.shape[0]} all unique addresses to {openai.model} (snapshot below):", "\n")
        print(df.to_string(index=False), "\n")
        openai.generate_summary_from_openai(ADVISOR_SYSTEM_PROMPT, df_json)

if __name__ == "__main__":
    main()