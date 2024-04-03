# JAWS
![hehe](/assets/ohey.jpeg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), CSV, and Graph Databases (Neo4j) to provide: A shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about finding anomalies.


## Setup

JAWS uses `pyshark` which requires tshark, which can be installed with Wireshark.

JAWS also uses Neo4j graph database. You can setup and run neo4j locally using, https://neo4j.com/product/developer-tools/ -- The scripts all point to the default setup.

To use `neonet` (ipinfo), OpenAI, or Hugging Face `transformers`, you will also need to sign up for those accounts and create env variables for:

- `IPINFO_API_KEY`
- `OPENAI_API_KEY`
- `HUGGINGFACE_KEY`


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

Run a local Neo4j Database, update references... the defaults are fine.

Run `neosea` to capture packets.

Run `neonet` to gather intel on IP Addresses. Uses IPInfo and requires a free api key.

Run `neotransform`, optionally passing `--api` with either `openai` or `starcoder`(default).

Run `neojawsx` to process embeddings and display cluster plots.


### Script descriptions

`neosea.py` -- Run with `neosea`. Stores packets in a CSV file `data/packets.csv`, as well as in a local or cloud-based Neo4j db. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation IP".

`neonet.py` -- Run with `neonet`. Passes `src_ip` to IPInfo and returns `org`, `hostname`, and `loc` -- Creating an Org node and OWNERSHIP relationship (relative to src_ip) to IP nodes in Neo4j. *REQUIRES your own IPInfo key.

`transformers/openai_embedding.py` -- Takes each packet and uses OpenAI Embeddings endpoint to transform them into embeddings, storing them back on the original entities in the Neo db. Uses concurrent batch processing; the current settings typically hit ~80% CPU for my setup (12th gen i5). *REQUIRES OpenAI API environment variables.

`transformers/starcoder.py` -- Same as the other `transform_`, but uses Huggingface StarCoder and local CUDA support to store the final hidden state from StarCoder as the packet embedding. This came about from a recommendation to try a Code Gen LLM. Initial testing suggests this outperforms OpenAI/GPT embeddings in terms of embedding fidelity. Commentary: In hindsight, this approach makes complete sense. Since a code gen LLM is trained on syntax and expected to output code or guidance relative to "technology", my hypothesis is that a code gen LLM inherently understands the structure of the packet better than a GPT. *REQUIRES Huggingface access to StarCoder/Huggingface API key.

`transformers/starcoder2_quant.py` -- Same as the other StarCoder transformer, but updated to use StarCoder2 and 4/8-bit quantization expriements.

`neojawsx.py` -- The TOOL. Performs PCA on the packet embeddings, uses nearest neighbor/knee to select EPS, then clusters using DBSCAN. Returns a 2D scatter plot with outliers called out as red markers and labeled with `org`, `domain/DNS`, `loc`, `route (IP:port(MAC) > IP:port(MAC))`, and `size`.


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

The following outputs crudely show what is happening inside of StarCoder by plotting hidden states and attentions across all layers.

No Payloads:

![Hidden states and attetion across layers, no payload](/assets/overview_no.png)

Hex Payloads:

![Hidden states and attetion across layers, hex payload](/assets/overview_hex.png)

Binary Payloads:

![Hidden states and attetion across layers, binary payload](/assets/overview_bin.png)


### Observations

- Raw data cannot really be used at scale and is too noisy (lacks meaning/context I suppose).
- OpenAI embeddings appear to improve accuracy, they are also the most sensitive to EPS and still noisy.
- StarCoder (or other code gen LLM) appears to be the "best", most stable (low sensitivity to change in EPS) results.
- Payloads appear to add noise.


#### Screenshot

Diagram of pipeline/recommended workflow and screenshot of Neo4j graph:

![diagram of pipeline and Neo4j example](/assets/diagram_21724.png)