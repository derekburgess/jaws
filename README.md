# JAWS
![hehe](/assets/cover.jpg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), using Graph Databases (Neo4j currently) to provide: A shell pipeline for gathering network packets on a given interface and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. The name JAWS because "wireshark", and I like the original Jaws movie. Which to me is really anomalies and outliers.


## Setup

JAWS uses `pyshark` which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/).

JAWS also uses a Neo4j graph database. You can run the docker container, or install and run neo4j locally using their tool, https://neo4j.com/product/developer-tools/ -- The scripts all point to the default Neo4j setup using env variables, so either way, configure:

### Set Environment Variables

- `LOCAL_NEO4J_URI` (typically... bolt://localhost:7687)
- `LOCAL_NEO4J_USERNAME` (default: neo4j)
- `LOCAL_NEO4J_PASSWORD` (you set)


### Additional Services

To use `neonet`: [ipinfo](https://ipinfo.io/), `neotransform`: [OpenAI](https://platform.openai.com/overview), or [Hugging Face](https://huggingface.co/bigcode/starcoder2-15b) transformers, you will also need to sign up for those accounts and create env variables for:

- `IPINFO_API_KEY`
- `OPENAI_API_KEY`
- `HUGGINGFACE_KEY`


### Neo4j Docker Container

From the JAWS root directory run: 

`docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms`


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


## Running and Commands

Run `jaws-guide` for instructions and commend overview.


Run `jaws-capture` with `--interface` (Default: Ethernet), `--database` (Default: captures), and `--duration` (Default: 10) in seconds to capture packets.

`jaws-capture --interface "Ethernet" --database "captures" --duration 10`


Run `jaws-ipinfo` to gather intel on IP Addresses using: `ipinfo.io`. Defaults to the captures database.

`jaws-ipinfo --database "captures"`


Run `jaws-embedd` with `--api` and either `openai` or `starcoder` to transform either `--type` `orgs` or `packets` into embeddings. Defaults to the captures database and openai api.

`jaws-embedd --api "openai" --type "orgs" --database "captures"`

Note: `starcode` will cache and run `bigcode/starcoder2-15b`


Run `jaws-finder` to process embeddings and display cluster plots. Defaults to the captures database.

`jaws-finder --type "orgs" --database "captures"`


Run `jaws-advisor` with `--api` and either `openai` or `llama` to send network traffic snapshots and return network traffic analysis. Defaults to the captures database and openai api.

`jaws-advisor --api "openai" --database "captures"`

Note: `llama` will cache and run `meta-llama/Meta-Llama-3-8B-Instruct`


Run `jaws-clear` to clear the database...

`jaws-clear --database "captures"`
