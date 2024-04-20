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

To use `neonet`: [ipinfo](https://ipinfo.io/), `neotransform`: [OpenAI](https://platform.openai.com/overview), or [Hugging Face](https://huggingface.co/bigcode/starcoder2-15b) `transformers`, you will also need to sign up for those accounts and create env variables for:

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


### Script descriptions

`neosea.py` -- Run with `neosea`. Stores packets in the Neo4j database. Update the `interface`.


`neonet.py` -- Run with `neonet`. Passes `src_ip` to IPInfo and returns `org`, `hostname`, and `loc` -- Creating an Org node and OWNERSHIP relationship (relative to src_ip) to IP nodes in Neo4j. *REQUIRES your own IPInfo key.


`neotransform.py` -- Run with `neotrasnform` Takes each packet and uses either OpenAI Embeddings endpoint or Hugging Face StarCoder 2 (pulled locally, so watch out) to transform them into embeddings, storing them back on the original entities in the Neo db. OpenAI uses concurrent batch processing; the current settings typically hit ~80% CPU for my setup (12th gen i5). StarCoder 2 locally on cuda processes 1 packet set at a time. StarCoder 2 also uses 4/8-bit quantization.


`neojawsx.py` -- Run with `neojawsx`. Performs PCA on the packet embeddings, uses nearest neighbor/knee to select EPS, then clusters using DBSCAN. Returns a 2D scatter plot with outliers called out as red markers and labeled with `org`, `domain/DNS`, `loc`, `route (IP:port(MAC) > IP:port(MAC))`, and `size`.


### Working with Neo4j

The tool does not seek to replace the database management, in theory any graph database could be swapped in, replacing the cypher queries as needed. My testing leaned on the DBMS application provided at https://neo4j.com/product/developer-tools/, but see `Neo4j Docker Container` above to skip the developer tools.

From within the DBMS application you can also access the `Graph Apps` sub menu and the `Neo4j Browser`. From within the browser you can:
- View the network graph by clicking on any of the node labels.
- Hoving over entities will show properties and relationships.
- Run queries on the database outside of the jaws scripts, including a common debug query I use:

```
MATCH ()-[r]->()
WHERE r.embedding IS NOT NULL
REMOVE r.embedding
RETURN COUNT(*)
```


### Examples

Example packet string:

`packet = src_ip:src_port(src_mac) > dst_ip:dst_port(dst_mac) using: protocol(flags) sending: [hex payload] AND/OR [binary payload] AND/OR [ascii payload] AND/OR [http payload] at a size of: size with ownership: org, hostname(dns) lat, long`


Example packet embedding from StarCoder (reduced):

`embedding = [-0.6838231086730957, 0.619213342666626, -0.2213636040687561, 0.5388462543487549, 0.9698492288589478, -0.05627664178609848, 0.400848925113678, 0.42690804600715637, -0.2869364321231842, 0.14443190395832062, 0.19022825360298157, -0.37119436264038086, -0.8193771839141846, -0.3072223961353302, -0.43989384174346924, 0.700538694858551, 0.879992663860321, -0.6817106008529663, 0.17782720923423767, 0.3537529706954956, -0.38453713059425354, -0.890569269657135... ]`

~50(58) packet example, no payloads.

Left to right: Raw data, OpenAI, StarCoder. Starcoder shows "zoomed in" views ontop of the plot as the clusters are very tight.

No Payloads:

![58 packet example test using raw data, OpenAI, and StarCoder, no payloads](/assets/group_no.png)


Hex Payloads:

![58 packet example test using raw data, OpenAI, and StarCoder, hex payloads](/assets/group_hex.png)


Binary Payloads:

![58 packet example test using raw data, OpenAI, and StarCoder, binary payloads](/assets/group_bin.png)


### Observations

- Raw data cannot really be used at scale and is too noisy (lacks meaning/context I suppose).
- OpenAI embeddings appear to improve accuracy, they are also the most sensitive to EPS and still noisy.
- StarCoder (or other code gen LLM) appears to be the "best", most stable (low sensitivity to change in EPS) results.
- Payloads appear to add noise.
