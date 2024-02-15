# JAWS
![hehe](/assets/ohey.jpeg)

JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), CSV and Graph Databases (Neo4j) to provide: A shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including... scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about finding anomolies.

UPDATE 2/9/2024: Refactored everything into a new workflow using Neo4j, OpenAI, IpInfo. I moved the version where all data is stored in CSV files into the directory `csv`.

UPDATE 14/2/2024: Included example of using StarCoder (Code Gen LLM) over GPT like OpenAI to generate packet embeddings.

HYPOTHESIS: Code gen large language models (Like StarCoder), which are trained and fined-tuned to produce structured, code-like, results- transform packet data into higher fidelity embeddings compared to GPT's like GPT4. In turn, when used with methods like PCA and DBSCAN, decrease data volume requirements and compute speed relative to idenfitying network anolomies.

![hehe](/assets/tests.png)

`neosea.py` -- Stores packets in a CSV file `data/packets.csv`, as well as in a local or cloud based Neo4j db. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation ip". Also update any packet details you wish to capture.

`transform_openai.py` -- Takes "round trip" packets and uses OpenAI Embeddings endpoint to transform them into embeddings, storing them back on the original entities in the neo db. Uses concurrent batch processing to keep pace with `neosea`, not a ton of testing here, the current settings typically hit ~80% CPU for my setup.

`transform_starcoder.py` -- Same as the other transform, but uses Huggingface StarCoder and local CUDA support to store the final hidden state from StarCoder as the embedding.

`neojawsx.py` --  Very different from the original 4 scripts, but more insightful in some ways... Performs PCA on the packet embeddings, used Kneed to select EPS, then clusters using DBSCAN. Returns a 2D scatter plot with outliers called out as red markers and labeled with `ip`, `dns`, `org`, and `loc` from IPInfo.

`util/non_embedding_test.py` -- Demonstrates performance of raw packet anaylsis, for comparing against `embedding_test.py` which performs the same test using embeddings.

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