# JAWS
![hehe](/assets/cover.jpg)

JAWS is a shell pipeline for gathering network packets and storing them in a graph database (Neo4j), with the goal of understanding various shapes of the network including scatter plots, nearest neighbor, DBSCAN outlier, subgraph analysis, and directed network graphs.


## Setup

JAWS uses pyshark which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/).

JAWS also uses a Neo4j graph database. You can run the provided Neo4j docker container, or install and run neo4j locally using their tool: https://neo4j.com/product/developer-tools/ -- The scripts all point to the default Neo4j setup using env variables, so either way, configure:


### Set Environment Variables

- `NEO4J_URI` (bolt://localhost:7687)
- `NEO4J_USERNAME` (neo4j)
- `NEO4J_PASSWORD` (you set)


### Additional Services

To use jaws-ipinfo, you will need to sign up for an account with [ipinfo](https://ipinfo.io/), and create an env variable for:

- `IPINFO_API_KEY`


Both jaws-compute (text-embedding-3-large) and jaws-advisor (gpt-3.5-turbo-16k) are set to pass --api openai by default. These commands require that you have an OpenAI account and create an env variable for: 

- `OPENAI_API_KEY`


Lastly, jaws-finder displays several plots using Matplot, but also saves those plots to a directory/endpoint of your choice, using:

- `JAWS_FINDER_ENDPOINT`


Optional: By passing --api transformers, 2 of the commands can pull and run local models from Hugging Face. jaws-compute currently uses bigcode/starcoder2-3b to create embeddings and jaws-advisor currently uses meta-llama/Meta-Llama-3-8B-Instruct to act as an agent/assisstant. Both of the local models require a Hugging Face account and that you request access to each model. Feel free to adjust the model usage, but either way create an env variable for:

- `HUGGINGFACE_API_KEY`


### Loca/Host Installation

On windows, run: 

`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121`

Or visit, https://pytorch.org/get-started/locally/ to configure an installation for your system.


From the /jaws root directory, install dependencies:

`pip install -r requirements.txt`


Install support for Quantization:

`pip install -i https://pypi.org/simple/ bitsandbytes`


Install JAWS using:

`pip install .`


### Neo4j Docker Container

From the /jaws/harbor directory run: 

`docker build --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD -t jaws-neodbms .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 jaws-neodbms`

This will build a basic Neo4j Docker container that can communicate with a host system running JAWS, or the "JAWS container" mentioned below.


### Experimental JAWS Docker Container

The docker-compose file in the /jaws/ocean directory is currently a work in progress...

From the /jaws/jaws directory run:

`docker build -t jaws-image .`

`docker run --gpus 1 --network host --privileged --publish 5297:5297 --volume JAWS_FINDER_ENDPOINT:/home --name jaws-container --detach jaws-image`


Open a bash shell:

`docker exec -it jaws-container bash`


## Running and Commands

Run `jaws-guide` for the rest of the instructions and commend overview.
