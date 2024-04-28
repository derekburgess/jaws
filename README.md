# JAWS
![hehe](/assets/cover.jpg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), using Graph Databases (Neo4j currently) to provide: A shell pipeline for gathering network packets on a given interface and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs.


## Setup

JAWS uses `pyshark` which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/).

JAWS also uses a Neo4j graph database. You can run the docker container, or install and run neo4j locally using their tool, https://neo4j.com/product/developer-tools/ -- The scripts all point to the default Neo4j setup using env variables, so either way, configure:

### Set Environment Variables

- `LOCAL_NEO4J_URI` (typically... bolt://localhost:7687)
- `LOCAL_NEO4J_USERNAME` (default: neo4j)
- `LOCAL_NEO4J_PASSWORD` (you set)


### Additional Services

To use `jaws-ipinfo`, you will need to sign up for an account with [ipinfo](https://ipinfo.io/), and create env variables for:

- `IPINFO_API_KEY`

`jaws-compute` is defaulted to use bigcode/starcoder2-15b set to 8bit quant using BitsAndBytes.
`jaws-advisor` is defaulted to use meta-llama/Meta-Llama-3-8B-Instruct.

Both models require a Hugging Face account and that you request access to use each model. Feel free to adjust the model usage, Starcode-2-15b is very large... Either way, create env variables for:

- `HUGGINGFACE_KEY`

Lastly, both `jaws-compute` and `jaws-advisor` can be set to use `--type "openai"` and in turn, text-embedding-3-large and gpt-3.5-turbo-16k. This option requires that you have an OpenAI account and create env variables for: 

- `OPENAI_API_KEY`


### Installation

Install dependencies:

`pip install -r requirements.txt`


I've tried to make everything CPU friendly, but if you want to use CUDA, install Pytorch for CUDA 12+

`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121`

Or visit, https://pytorch.org/get-started/locally/ to configure an installation for your system.


Install dependencies for quantization:

`pip install -i https://pypi.org/simple/ bitsandbytes`


Install JAWS:

`pip install -e .`


### Neo4j Docker Container

From the JAWS root directory run: 

`docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms`


## Running and Commands

Run `jaws-guide` for instructions and commend overview.
