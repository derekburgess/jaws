# JAWS
![hehe](/assets/ohey.jpeg)

Shell pipeline for gathering network packets on a given `interface` and understanding various shapes of the network including... scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. I went with the "Jaws Theme" because I like Jaws the movie, which to me, like Moby Dick, is about anomolies.

UPDATE 2/9/2024: Refactored everything into a new workflow using Neo4j, OpenAI, IpInfo. I moved the version where all data is stored in CSV files into the directory `csv`.

`neosea.py` -- Stores packets in a CSV file `data/packets.csv`, as well as in a local or cloud based Neo4j db. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation ip". Also update any packet details you wish to capture.

`util/chum.py` -- This script in conjunctuon with `listner.py`, and any "remote server" (I've been testing with a free EC2 instance at no cost...), can help simulate "exfiltration events". In addition, the `neosea.py` script, when given this `ip address`, will label the data accordingly... eiter `BASE` pr `CHUM`...

`transform.py` -- Takes "round trip" packets and uses OpenAI Embeddings endpoint to transform them into embeddings, storing them back on the original entities in the neo db.

`neojaws.py` -- Very different from the original 4 scripts, but more insightful in some ways... This takes the packet embeddings and performs PCA(and DBSCAN), returning a 2D scatter plot labeled with `ip`, `dns`, `org`, and `loc` from IPInfo.

`util/_neofinder.py` -- Neo4j version of the original finder script. Simple 2D scatter showing `port` and `size`.

`util/_neoscan.py` -- Neo4j version of the original scan script, performs PCA/DBSCAN on pre-embedding data.

The `data` directory is not included, create or change the path as needed. Only pertains to CSV storage.

In the `csv` directory:

`sea.py` -- The main file. Update the `batch`, `interface`, and if you use the `chum.py` script, your AWS or "exfiltration simulation ip". Also update any packet details you wish to capture.

`finder.py` -- Simple 2D scatter showing `port` and `size`. It also handled `BASE` and `CHUM` packets differently, coloring the chum packets red. For quickly seeing what is going on and how the data set is shapping up. Also for exploring port activity.

`scan.py` -- PCA and DBSCAN with `dst_port` pulled out and used as the outlier label. Displays as a polar plot for thematics... For exploring how the data is clustering and which ports stand out.

`sub.py` -- Perform subgraph analysis with in and out degree, outputs a CSV file and a directed graph visual. For topology of the network.

`jaws.py` -- Simple Random Forest classifier script currently set to `dst_port` and `size`, returning `dst_ip`. Adjust the variables!

`timeseries.py` -- For a given `ip address`, returns packet size over time.

`report.py` -- For a given `ip address`, returns OSINT...