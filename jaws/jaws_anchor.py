import argparse
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.jaws_config import *

def download_model(model):
    models = {
        PACKET_MODEL_ID: PACKET_MODEL,
        LANG_MODEL_ID: LANG_MODEL
    }
    
    try:
        AutoTokenizer.from_pretrained(models[model])
        AutoModelForCausalLM.from_pretrained(models[model])
        print(f"\nSuccessfully downloaded {models[model]}", "\n")
    except Exception as e:
        print(f"\nFailed to download: {models[model]}", "\n")
        print(e, "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download model files from Hugging Face.")
    parser.add_argument("--model", choices=[PACKET_MODEL_ID, LANG_MODEL_ID], help="Specify a model to download.")
    args = parser.parse_args()
    download_model(args.model)