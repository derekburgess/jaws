import os
import argparse
import warnings
from transformers import AutoTokenizer, AutoModelForCausalLM


huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
starcoder = "bigcode/starcoder2-3b"
llama = "meta-llama/Meta-Llama-3.1-8B-Instruct"


def pull_model(model_name, model_path):
    try:
        AutoTokenizer.from_pretrained(model_path, token=huggingface_token, use_fast=False)
        AutoModelForCausalLM.from_pretrained(model_path, token=huggingface_token)
        print(f"\nSuccessfully downloaded {model_name}", "\n")
    except Exception as e:
        print(f"\nFailed to download {model_name}: {e}", "\n")


def pull_model_files(model):
    models = {
        "starcoder": starcoder,
        "llama": llama
    }
    
    if model == "all":
        for name, path in models.items():
            pull_model(name, path)
    elif model in models:
        pull_model(model, models[model])
    else:
        print("Invalid model name. Please choose 'starcoder', 'llama', or 'all'.")


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    
    parser = argparse.ArgumentParser(description="Download model files from Hugging Face, either StarCoder2-3b or Llama-3-8B-Instruct.")
    parser.add_argument("--model", choices=["starcoder", "llama", "all"], default="all", help="Specify which model to download, either starcoder or llama (default: all)")

    args = parser.parse_args()

    pull_model_files(args.model)

if __name__ == "__main__":
    main()