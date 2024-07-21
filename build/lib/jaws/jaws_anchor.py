import os
import argparse
import warnings
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig


huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
starcoder = "bigcode/starcoder2-3b"
llama = "meta-llama/Meta-Llama-3-8B-Instruct"


def pull_starcoder():
    try:
        AutoTokenizer.from_pretrained(starcoder, token=huggingface_token, use_fast=False)
        AutoModelForCausalLM.from_pretrained(starcoder, token=huggingface_token)
    except Exception as e:
        print(f"Failed to download: {e}")

def pull_llama():
    try:
        AutoTokenizer.from_pretrained(llama, token=huggingface_token, use_fast=False)
        AutoModelForCausalLM.from_pretrained(llama, token=huggingface_token)
    except Exception as e:
        print(f"Failed to download: {e}")


def pull_model_files(model):
    if model == "starcoder":
        pull_starcoder()
    elif model == "llama":
        pull_llama()
    elif model == "all":
        pull_starcoder()
        pull_llama()
    else:
        print("Invalid model name. Please choose either 'starcoder' or 'llama'.")


def main():
    warnings.filterwarnings("ignore", category=FutureWarning)
    
    parser = argparse.ArgumentParser(description="Download model files from Hugging Face, either StarCoder2-3b or Llama-3-8B-Instruct.")
    parser.add_argument("--model", choices=["starcoder", "llama", "all"], default="all",
                    help="Specify which model to download, either starcoder or llama (default: all)")

    args = parser.parse_args()

    pull_model_files(args.model)


if __name__ == "__main__":
    main()