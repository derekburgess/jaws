import os
import argparse
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig


huggingface_token = os.getenv("HUGGINGFACE_API_KEY")
quantization_config = BitsAndBytesConfig(load_in_8bit=True)
starcoder = "bigcode/starcoder2-3b"
llama = "meta-llama/Meta-Llama-3-8B-Instruct"


def pull_starcoder():
        try:
            AutoTokenizer.from_pretrained(starcoder, token=huggingface_token)
            AutoModelForCausalLM.from_pretrained(starcoder, quantization_config=quantization_config, token=huggingface_token)
        except Exception as e:
            print(f"{e}")

def pull_llama():
        try:
            AutoTokenizer.from_pretrained(llama, token=huggingface_token)
            AutoModelForCausalLM.from_pretrained(llama, token=huggingface_token)
        except Exception as e:
            print(f"{e}")


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
    parser = argparse.ArgumentParser(description="Download model files from Hugging Face, either StarCoder2-3b or Llama-3-8B-Instruct.")
    parser.add_argument("--model", choices=["starcoder", "llama", "all"],
                    help="Specify which model to download, either starcoder or llama (default: None)")

    args = parser.parse_args()

    pull_model_files(args.model)


if __name__ == "__main__":
    main()