# JAWS
![hehe](/assets/ohey.jpeg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), using Graph Databases (Neo4j currently) to provide: A shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about finding anomalies.


## Setup

JAWS uses `pyshark` which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/).

JAWS also uses Neo4j graph database. You can setup and run neo4j locally using, https://neo4j.com/product/developer-tools/ -- The scripts all point to the default setup, but are env variables, so configure:

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

Alternatively you can use the Neo4j Docker Image. To do so, run `docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .` from the JAWS root directory. Then run `docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms`. `-t neojawsdbms` tags the container as such, where `--name captures` is a specific container with the `captures` database running. I'll probably expand on this later to be more effecient. With the docker container running, everything should work out of the box. You can also connect to it using the Neo4j developer-tools and browser, its all very easy, so I wont explain here.


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

Run a local Neo4j Database...

All of the commands can accept `--database`, which defaults to `captures`.


Run `neosea` with `--interface` to capture packets.

`neosea --interface "Ethernet" --database "captures"`


Run `neonet` to gather intel on IP Addresses using: `ipinfo.io`

`neonet --database "captures"`


Run `neotransform` with `--api` and either `openai` or `starcoder`(default) to transform packets into embeddings.

`neotransform --api "openai" --database "captures"`


Run `neojawsx` to process embeddings and display cluster plots.

`neojawsx --database "captures"`


Run `neosink` to clear the database...

`neosink --database "captures"`
