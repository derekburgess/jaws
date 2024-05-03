import os
import subprocess

def login_to_huggingface(token):
    command = f"huggingface-cli login --token {token}"
    subprocess.run(command, shell=True)

def pull_models():
    models = ["bigcode/starcoder2-3b", "meta-llama/Meta-Llama-3-8B-Instruct"]
    for model in models:
        command = f"huggingface-cli hf hub 'pull' '{model}'"
        subprocess.run(command, shell=True)

if __name__ == "__main__":
    huggingface_token = os.getenv("HUGGINGFACE_KEY")
    login_to_huggingface(huggingface_token)
    pull_models()
