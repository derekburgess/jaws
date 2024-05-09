# JAWS
![hehe](/assets/cover.jpg)

JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for the purpose of identifying outliers. It gathers and stores packets/osint in a graph database (Neo4j). It also provides a set of commands to transform and process packets into plots and reports using: K-means, DBSCAN, OpenAI, StarCoder2, and Llama3. It is indended to be run locally using open models, but is set to run using OpenAI by default for demos and easy of use.


## Setup

JAWS uses pyshark which requires tshark, which can be installed with [Wireshark](https://www.wireshark.org/). Or by installing the tshark package.

JAWS also uses Neo4j as the graph database. You can run the provided Neo4j docker container (See below), or install and run the [Neo4j dbms and GUI](https://neo4j.com/product/developer-tools/).

As mentioned above, JAWS optionally uses [Docker](https://www.docker.com/).


### Set Neo4j Environment Variables

`NEO4J_URI` (bolt://localhost:7687)


`NEO4J_USERNAME` (neo4j)


`NEO4J_PASSWORD` (you set)


### Set Environment Variables for Additional Services

To run jaws-ipinfo, you will need to sign up for a free account with [ipinfo](https://ipinfo.io/), and create an env variable for:

`IPINFO_API_KEY`


Both jaws-compute (text-embedding-3-large) and jaws-advisor (gpt-3.5-turbo-16k) are set to pass --api openai by default. These commands require that you have an OpenAI account (not free) and create an env variable for: 

`OPENAI_API_KEY`


Lastly, jaws-finder displays several plots using Matplot, but also saves those plots to a directory/endpoint of your choice, using:

`JAWS_FINDER_ENDPOINT`


Optional: Since OpenAI is not free, by passing --api transformers, 2 of the commands can pull and run local models from Hugging Face. jaws-compute currently uses bigcode/starcoder2-3b to create embeddings and jaws-advisor currently uses meta-llama/Meta-Llama-3-8B-Instruct to act as an agent/assisstant. Both of the local models require a Hugging Face account and that you request access to each model. Feel free to adjust the model usage, but either way create an env variable for:

`HUGGINGFACE_API_KEY`


### Install the JAWS Python Package

On Windows, run: 

`pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121`

Or visit, https://pytorch.org/get-started/locally/ to configure an installation for your system.


From the /jaws root directory, install dependencies:

`pip install -r requirements.txt`


Install support for Quantization(StarCoder):

`pip install -i https://pypi.org/simple/ bitsandbytes`


Install JAWS using:

`pip install .`


If you are using the Neo4j dbms and GUI, that is it, you can skip the Docker steps and run jaws-guide for the rest of the instructions and commend overview.


### Neo4j Docker Container

This Docker container operates as a local/headless Neo4j database. You can run all commands against it by default and easily connect to and view the graph using the Neo4j GUI.

From the /jaws/harbor directory run: 

`docker build -t jaws-neodbms --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD --build-arg DEFAULT_DATABASE=captures .` 


Then run: 

`docker run --name captures -p 7474:7474 -p 7687:7687 --detach jaws-neodbms`


If you plan to run the Hugging Face models on your local machine that is it, you can skip the next step and run jaws-guide for the rest of the instructions and commend overview.


### JAWS Compute Docker Container

This Docker container operates as a full instance of JAWS. However, the intended purpose is for providing a deployable container for compute resources.


From the /jaws/ocean directory run:

`docker build -t jaws-image --build-arg NEO4J_URI --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD --build-arg IPINFO_API_KEY --build-arg OPENAI_API_KEY --build-arg HUGGINGFACE_API_KEY .`


`docker run --gpus 1 --network host --name jaws-container --detach jaws-image`


To pull the Hugging Face models, run jaws-anchor, which will pull everything by default, or append --model starcoder or llama.


`docker exec -it jaws-container jaws-anchor`


To use the container run:

`docker exec -it jaws-container jaws-compute --api "transformers"`


`docker exec -it jaws-container jaws-advisor --api "transformers"`


## Usage

`jaws-guide`

The Neo4j Docker Container acts as the central graph. JAWS is then run a host to collect packets. The jaws-compute container can be deployed to manage models and compute resourses.