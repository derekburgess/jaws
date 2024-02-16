# JAWS
![hehe](/assets/ohey.jpeg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), CSV and Graph Databases (Neo4j) to provide: A shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including... scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about finding anomolies.

`neosea.py` -- Stores packets in a CSV file `data/packets.csv`, as well as in a local or cloud based Neo4j db. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation ip". Also update any packet details you wish to capture.

`neonet.py` -- Passes `src_ip` to IPInfo and returns `org`, `hostname`, and `loc` -- Creating an Org node and OWNERSHIP relationship to IP nodes in Neo4j.

`util/hex.py` -- Attempts to convert hexidecimal payloads into ASCII... I really need to figure out what I want to do with payloads...

`transform_openai.py` -- Takes each packet and uses OpenAI Embeddings endpoint to transform them into embeddings, storing them back on the original entities in the neo db. Uses concurrent batch processing, the current settings typically hit ~80% CPU for my setup.

`transform_starcoder.py` -- Same as the other `transform_`, but uses Huggingface StarCoder and local CUDA support to store the final hidden state from StarCoder as the packet embedding. This came about from a recommendation to try a Code Gen LLM. Initial testing suggests this out performs OpenAI/GPT embeddings in terms of emebedding fidelity.

`neojawsx.py` --  Performs PCA on the packet embeddings, uses Nearst Neighbor/Kneed to select EPS, then clusters using DBSCAN. Returns a 2D scatter plot with outliers called out as red markers and labeled with `org`, `domain/dns`, `loc`, `route (ip:port(mac) > ip:port(mac))`, and `size`.

`util/ne_test.py` -- Or non-embedding based test, demonstrates performance of raw packet anaylsis, for comparing against `etest.py` which performs the same test using embeddings. Does not include labels, so great for public sharing.

`util/chum.py` -- This script in conjunctuon with `listner.py`, and any "remote server" (I've been testing with a free EC2 instance at no cost...), can help simulate "exfiltration events". In addition, the `neosea.py` script, when given this `ip address`, will label the data accordingly... eiter `BASE` pr `CHUM`...

The `data` directory is not included, create or change the path as needed. Only pertains to CSV storage.

In the `csv` directory:

`sea.py` -- The main file. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation ip". Also update any packet details you wish to capture.

`finder.py` -- Simple 2D scatter showing `port` and `size`. It also handled `BASE` and `CHUM` packets differently, coloring the chum packets red. For quickly seeing what is going on and how the data set is shapping up. Also for exploring port activity.

`scan.py` -- PCA and DBSCAN with `dst_port` pulled out and used as the outlier label. Displays as a polar plot for thematics... For exploring how the data is clustering and which ports stand out.

`sub.py` -- Perform subgraph analysis with in and out degree, outputs a CSV file and a directed graph visual. For topology of the network.

`jaws.py` -- Simple Random Forest classifier script currently set to `dst_port` and `size`, returning `dst_ip`. Adjust the variables!

`timeseries.py` -- For a given `ip address`, returns packet size over time.

`report.py` -- For a given `ip address`, returns OSINT...