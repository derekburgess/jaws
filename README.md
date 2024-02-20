# JAWS
![hehe](/assets/ohey.jpeg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), CSV, and Graph Databases (Neo4j) to provide: A shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about finding anomalies.

`neosea.py` -- Stores packets in a CSV file `data/packets.csv`, as well as in a local or cloud-based Neo4j db. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation IP".

`neonet.py` -- Passes `src_ip` to IPInfo and returns `org`, `hostname`, and `loc` -- Creating an Org node and OWNERSHIP relationship (relative to src_ip) to IP nodes in Neo4j.

`util/hexbin.py` -- Attempts to convert hexadecimal payloads into Binary and ASCII... My current hypothesis is that the binary payloads are less noisy downstream. Moved this script to util/ as it is optional/for testing.

`transform_openai.py` -- Takes each packet and uses OpenAI Embeddings endpoint to transform them into embeddings, storing them back on the original entities in the Neo db. Uses concurrent batch processing; the current settings typically hit ~80% CPU for my setup (12th gen i5).

`transform_starcoder.py` -- Same as the other `transform_`, but uses Huggingface StarCoder and local CUDA support to store the final hidden state from StarCoder as the packet embedding. This came about from a recommendation to try a Code Gen LLM. Initial testing suggests this outperforms OpenAI/GPT embeddings in terms of embedding fidelity. Commentary: In hindsight, this approach makes complete sense. Since a code gen LLM is trained on syntax and expected to output code or guidance relative to "technology", my hypothesis is that a code gen LLM inherently understands the structure of the packet better than a GPT.

`neojawsx.py` -- Performs PCA on the packet embeddings, uses nearest neighbor/knee to select EPS, then clusters using DBSCAN. Returns a 2D scatter plot with outliers called out as red markers and labeled with `org`, `domain/DNS`, `loc`, `route (IP:port(MAC) > IP:port(MAC))`, and `size`.

`util/ne_test.py` -- Or `non-embedding test`, demonstrates the performance of raw packet analysis, for comparing against `etest.py` which performs the same test using embeddings. Does not include labels. Great for public demonstration.

`util/chum.py` -- This script in conjunction with `listener.py`, and any "remote server" (I've been testing with a free EC2 instance at no cost...), can help simulate "exfiltration events". In addition, the `neosea.py` script, when given this `IP address`, will label the data accordingly... either `BASE` or `CHUM`...

The `data` directory is not included, create or change the path as needed. Only pertains to CSV storage.

50 packet example:

![50 packet example test using raw data, OpenAI, and StarCoder](/assets/test50.png)

500 packet example:

![500 packet example test using raw data, OpenAI, and StarCoder](/assets/test500.png)

Observations:
- Raw data cannot really be used at scale and is too noisy (lacks meaning/context I suppose).
- OpenAI embeddings are a decent improvement and easiest to implement. While this approach improves accuracy, it is also the most sensitive to EPS and still noisy.
- StarCoder (or other code gen LLM) appears to be the "best", most stable (low sensitivity to change in EPS) results. Do not be fooled by the sparse plot, zoom into those clusters (or check the db) to better understand embedding fidelity.

Diagram of pipeline/recommended workflow and screenshot of Neo4j graph:

![diagram of pipeline and Neo4j example](/assets/diagram_21724.png)

Example packet string:

`packet = src_ip:src_port(src_mac) > dst_ip:dst_port(dst_mac) using: protocol(flags) sending: [hex payload] AND/OR [binary payload] AND/OR [ascii payload] AND/OR [http payload] at a size of: size with ownership: org, hostname(dns) lat, long`

Note: Sending all of the Payload options to OpenAI will often trigger the token limit error... Have seen packet payloads exceed 15,000 tokens and the OpenAI embeddings end-point has a max 8750.

Example packet embedding from StarCoder (reduced):

`embedding = [-0.6838231086730957, 0.619213342666626, -0.2213636040687561, 0.5388462543487549, 0.9698492288589478, -0.05627664178609848, 0.400848925113678, 0.42690804600715637, -0.2869364321231842, 0.14443190395832062, 0.19022825360298157, -0.37119436264038086, -0.8193771839141846, -0.3072223961353302, -0.43989384174346924, 0.700538694858551, 0.879992663860321, -0.6817106008529663, 0.17782720923423767, 0.3537529706954956, -0.38453713059425354, -0.890569269657135... ]`

StarCoder activation and attention, all layers across heads:

![Activation and attetion across layers for the packet string](/assets/starcoder_packet.png)
