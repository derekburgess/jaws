# JAWS
![hehe](/assets/cover.jpg)


## 2025

Refactored the experience to become more agentic. Along with the CLI commands/UX, the agent commands specifically demonstrates agents capable of collecting data, using tools, and providing analysis.

Running `jaws-smolagent` utilizes the [smolagents](https://huggingface.co/docs/smolagents/en/index) library to orchistrate 2 agents in a managed hand-off workflow. A manager agent hands the task off to a network analyst, who collects and prepares the data, handing that data back off to the manager, who returns a report.

Running `jaws-agent` utilizes Microsoft's [Semantic Kernel](https://github.com/microsoft/semantic-kernel) library to explore concurrent, hand-off and group chat orchsitration. Creates an interactive command center at `http://127.0.0.1:7860/`. With concurrent, 2 models work in parallel to capture data and report on red flags. With hand-off, a lead model manages 2 assistants to create a final report. With group chat, 3 models work collaboratively to create a final report.

Note, both agents send emails containing the final report, you will need to set these variables for that to work: `EMAIL_SENDER`, `EMAIL_RECIPIENT`, `EMAIL_PASSWORD`, `EMAIL_SERVER`, `EMAIL_PORT`.


## Context

JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for the purpose of identifying outliers. It also works as a Graph RAG, utilizing OpenAI or local Transformers. It gathers and stores packets/osint in a graph database (Neo4j). It provides a set of commands to transform and process packets into plots and reports using: K-means, DBSCAN, OpenAI, StarCoder2, etc. It is intended to run locally using "open" models, but is set to run using OpenAI by default for demos and easy of use.


## Prerequisites and initial setup

This part of the guide is assuming a clean install and mainly exists as a guide for myself when setting up new systems.


### CUDA Support

If you plan on running local models against a NVIDIA GPU, you will need the CUDA Toolkit installed. [You can configure an installer or guide here](https://developer.nvidia.com/cuda-downloads) -- On Ubuntu, if you installed the additional drivers for NVIDIA, you can run:

`apt install nvidia-cuda-toolkit`


### Wireshark

JAWS uses pyshark which requires termshark, which can be installed with [Wireshark](https://www.wireshark.org/). Termshark is an optional installation and bundled with the executables for Windows and Mac. On Ubuntu you can install both using:

`apt install wireshark` and `apt install termshark`

Wireshark will ask you about adding non-root users to the Wireshark group. It is recommended that you say Yes. If you said No, then you can run:

`dpkg-reconfigure wireshark-common`

In addition, the installation and adding non-root users is suppose to add your user to the Wireshark group and set permissions, but I have found that it doesn't always do this. You may need to run:

`adduser $USER wireshark` and `chmod +x /usr/bin/dumpcap`


### Neo4j DBMS

JAWS also uses Neo4j as the graph database. You can run the provided Neo4j docker container (See below), or install and run the [Neo4j DBMS/Desktop app](https://neo4j.com/product/developer-tools/) on Windows/Mac/Linux.

On Ubuntu, you can follow these instructions for installing the package: 

https://neo4j.com/docs/operations-manual/current/installation/linux/debian/#debian-installation

To use the desktop application on Linux, you will need to set its permissions:

`chmod +x neo4j...`

Ubuntu may complain about lack of [FUSE](https://github.com/AppImage/AppImageKit/wiki/FUSE).

Ubuntu may also complain about lack of sandbox... So far I have only found running the app image with `--no-sandbox` appended.

Additionally, the problem appears to stem from changes made to Ubuntu 24.04, this command, recommended in the Docker documentation, which appears to disable "AppArmor", also resolves the issue:

`sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`


### Conda/Anaconda/Miniconda

I tend to use [Anaconda](https://www.anaconda.com/download/success) and prefer their installation script over the guide...

If you used the installation script, add Conda to bash: `nano ~/.bashrc` and append `export PATH=~/anaconda3/bin:$PATH` to the end of the file, replacing /anaconda3/bin with your actual installation path.

Some other useful(and basic) Conda commands:

`conda create --name env_name python=version`

`conda activate env_name` and `conda deactivate`

`conda env config vars list` and `conda env config vars set ENV_VAR=value`

Finally, if you want to use the navigator GUI, it can be installed using: 

`conda install anaconda-navigator` and `anaconda-navigator`


### Docker

As mentioned above, JAWS optionally uses [Docker](https://www.docker.com/). Again, easy enough to figure out.

Some useful (and basic) Docker commands:

`docker ps -a` and `docker volume ls`

`docker inspect` and `docker stop`

`docker rm` and `docker volume rm`

`docker system prune -a --volumes`


### Set Neo4j Environment Variables

`NEO4J_URI` (bolt://localhost:7687)

`NEO4J_USERNAME` (neo4j)

`NEO4J_PASSWORD` (you set)


### Set Environment Variables for Additional Services

To run jaws-ipinfo, you will need to sign up for a free account with [ipinfo](https://ipinfo.io/), and create an env variable for:

`IPINFO_API_KEY`


Both jaws-compute (text-embedding-3-large) and the jaws-advisor (gpt-4.1) are set to use OpenAI by default. These commands require that you have an OpenAI account (not free) and create an env variable for: 

`OPENAI_API_KEY`


Somewhat Optional: Since OpenAI is not free, by passing --api transformers, or jaws-utils --download model, jaws can download and run on device models from Hugging Face. jaws-compute currently uses bigcode/starcoder2-3b to create embeddings. Feel free to adjust the model usage, but either way create an env variable for:

`HUGGINGFACE_API_KEY`

In addition, you will want to install the Hugging Face CLI, found here: https://huggingface.co/docs/huggingface_hub/en/guides/cli and login. This saves on passing the key.

`pip install -U "huggingface_hub[cli]"`

`huggingface-cli login`


The command jaws-finder displays several plots using Matplot, but also saves those plots to a directory/endpoint of your choice, using:

`JAWS_FINDER_ENDPOINT`


### Install the JAWS Python Package


From the /jaws root directory, install dependencies:

`pip install -r requirements.txt`


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


To pull the Hugging Face models, run jaws-utils with the model argument, either: starcoder or phi4.

`docker exec -it jaws-container jaws-utils --model starcoder`


To use the container run:

`docker exec -it jaws-container jaws-compute --api "transformers"`


## Usage

`jaws-guide`
