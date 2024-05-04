# JAWS
![hehe](/assets/cover.jpg)

JAWS is a shell pipeline for gathering network packets and storing them in a graph database (Neo4j), with the goal of understanding various shapes of the network including scatter plots, nearest neighbor, DBSCAN outlier, subgraph analysis, and directed network graphs.


## Setup

JAWS uses `pyshark` which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/).

JAWS also uses a Neo4j graph database. You can run the provided Neo4j docker container, or install and run neo4j locally using their tool: https://neo4j.com/product/developer-tools/ -- The scripts all point to the default Neo4j setup using env variables, so either way, configure:

### Set Environment Variables

- `NEO4J_URI` (bolt://localhost:7687)
- `NEO4J_USERNAME` (neo4j)
- `NEO4J_PASSWORD` (you set)


### Additional Services

To use `jaws-ipinfo`, you will need to sign up for an account with [ipinfo](https://ipinfo.io/), and create an env variable for:

- `IPINFO_API_KEY`


Both `jaws-compute` (text-embedding-3-large) and `jaws-advisor` (gpt-3.5-turbo-16k) are set to use `--api "openai"` by default. These commands require that you have an OpenAI account and create an env variable for: 

- `OPENAI_API_KEY`


Optional: By passing `--api transformers`, both scripts can pull and run local models from Hugging Face. `jaws-compute` currently uses `bigcode/starcoder2-3b` to create embeddings and `jaws-advisor` currently uses `meta-llama/Meta-Llama-3-8B-Instruct` to return NLP. Both of the local models require a Hugging Face account and that you request access to use each model. Feel free to adjust the model usage, but either way create an env variable for:

- `HUGGINGFACE_API_KEY`


Lastly, `jaws-finder` displays several plots using Matplot, but also saves those figures to a directory of your choice, using:

- `JAWS_FINDER_ENDPOINT`


### Installation

Install dependencies:

`pip install -r requirements.txt`


Note that JAWS was developed against Nvidia/CUDA but should also work on CPU. On Linux/Mac and some systems (mainly embedded linux systems I have tested on) torch will fail to install through requirements.txt, so if you are also on one of these bespoke systsmes (such as arm/rpi), first run: `pip install torch`

On windows, run: `pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121` -- Or visit, https://pytorch.org/get-started/locally/ to configure an installation for your system.

If you plan to run `--api transformers`, Note that StarCoder2-3b is set to use 8-bit quanitzation and on some systems, mainly windows(?), you may need to use: `pip install -i https://pypi.org/simple/ bitsandbytes`.


Install JAWS using:

`pip install .`


### Neo4j Docker Container

The Neo4j setup really only supports 1 container/dbms running at a time ("captures"), the commands currently do not support passing all of the configurations needed. All of the commands do accept the `--database` flag, which defaults to "captures". Operating a single container and running the commands, ignoring the --database flag altogether, should just work out of the box. Changing "captures" in the dockerfile and in the commands below, will require passing that database name with every command.

From the JAWS project directory run: 

`docker build --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD -t jaws_neodbms .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 jaws_neodbms`


### JAWS Docker Container

The "harbor" directory is currently a work in progress...

From the root directory run:

`docker-compose up`

Once the containers are running, run:

`docker ps`

Use the container id to then open a shell:

`docker exec -it <container_id> bash`


## Running and Commands

Run `jaws-guide` for instructions and commend overview.
