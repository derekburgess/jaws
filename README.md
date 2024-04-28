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

To use `jaws-ipinfo`, you will need to sign up for an account with [ipinfo](https://ipinfo.io/), and create an env variable for:

- `IPINFO_API_KEY`


Both `jaws-compute` (text-embedding-3-large) and `jaws-advisor` (gpt-3.5-turbo-16k) are set to use `--api "openai"` by default. These commands require that you have an OpenAI account and create an env variable for: 

- `OPENAI_API_KEY`


Optional: By passing `--api`, both scripts can pull and run local models. Passing `starcoder` to `jaws-compute` will use bigcode/starcoder2-3b and passing `llama` to `jaws-advisor` will use meta-llama/Meta-Llama-3-8B-Instruct. Both of the local models require a Hugging Face account and that you request access to use each model. Feel free to adjust the model usage, but either way create an env variable for:

- `HUGGINGFACE_KEY`


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

The Neo4j setup really only supports 1 container/dbms running at a time ("captures"), the commands currently do not support passing all of the configurations needed. All of the commands do accept the `--database` flag, which defaults to "captures". Run the commands and operating a single container, ignoring the --database flag altogether, should work. Changing "captures" in the dockerfile and in commands below, will require passing that database name with every command.

From the JAWS root directory run: 

`docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms`


## Running and Commands

Run `jaws-guide` for instructions and commend overview.
