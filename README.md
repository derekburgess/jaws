# JAWS
![hehe](/assets/cover.jpg)

JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for the purpose of identifying outliers. It also works as Graph RAG utilizing OpenAI or local Transformers. It gathers and stores packets/osint in a graph database (Neo4j). It also provides a set of commands to transform and process packets into plots and reports using: K-means, DBSCAN, OpenAI, StarCoder2, and Llama3. It is intended to run locally using open models, but is set to run using OpenAI by default for demos and easy of use.


## Prerequisites and initial setup

JAWS uses pyshark which requires termshark, which can be installed with [Wireshark](https://www.wireshark.org/). Or by installing the termshark package.

On Linux, Wireshark will ask you about adding non-root users to the Wireshark group. It is recommended that you say Yes. If you said No, then you can run:

`sudo dpkg-reconfigure wireshark-common`

This is suppose to add your user to the Wireshark group, but I have found that is not always true. You can run:

`sudo adduser $USER wireshark`

`sudo chmod +x /usr/bin/dumpcap`

To install Terminalshark on Ubuntu run `apt install termshark`

JAWS also uses Neo4j as the graph database. You can run the provided Neo4j docker container (See below), or install and run the [Neo4j dbms and GUI](https://neo4j.com/product/developer-tools/). I wont cover the Ubuntu installation in detail, it's easy enough. In short, they provide an AppImage, make it executable:

`sudo chmod +x file` and run it. You can use that same AppImage to launch the Neo4j GUI as needed.

As mentioned above, JAWS optionally uses [Docker](https://www.docker.com/). Again, easy enough to figure out.

Create your environment of choice... Python env, Conda, etc.


### Set Neo4j Environment Variables

`NEO4J_URI` (bolt://localhost:7687)

`NEO4J_USERNAME` (neo4j)

`NEO4J_PASSWORD` (you set)


### Set Environment Variables for Additional Services

To run jaws-ipinfo, you will need to sign up for a free account with [ipinfo](https://ipinfo.io/), and create an env variable for:

`IPINFO_API_KEY`


Both jaws-compute (text-embedding-3-large) and jaws-advisor (gpt-4o) are set to pass --api openai by default. These commands require that you have an OpenAI account (not free) and create an env variable for: 

`OPENAI_API_KEY`


The command jaws-finder displays several plots using Matplot, but also saves those plots to a directory/endpoint of your choice, using:

`JAWS_FINDER_ENDPOINT`


Somewhat Optional: Since OpenAI is not free, by passing --api transformers, 2 of the commands can pull and run local models from Hugging Face. jaws-compute currently uses bigcode/starcoder2-3b to create embeddings and jaws-advisor currently uses meta-llama/Meta-Llama-3-8B-Instruct to act as an agent/assisstant. Both of the local models require a Hugging Face account and that you request access to each model. Feel free to adjust the model usage, but either way create an env variable for:

`HUGGINGFACE_API_KEY`

In addition, you will want to install the Hugging Face CLI, found here: https://huggingface.co/docs/huggingface_hub/en/guides/cli and login.

`pip install -U "huggingface_hub[cli]"`

`huggingface-cli login`


### Install the JAWS Python Package

Visit, https://pytorch.org/get-started/locally/ to configure an installation for your system.

`pip3 install torch torchvision torchaudio`


From the /jaws root directory, install dependencies:

`pip3 install -r requirements.txt`


Install support for Quantization(StarCoder):

`pip3 install -i https://pypi.org/simple/ bitsandbytes`


Install JAWS using:

`pip3 install .`


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


## Usage

`jaws-guide`

The Neo4j Docker Container acts as the central graph. JAWS is then run a host to collect packets. The jaws-compute container can be deployed to manage models and compute resourses.
