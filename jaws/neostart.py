def main():
    print("""
JAWS is a network analysis toolset that works with both CPU and GPU (CUDA), using Graph Databases (Neo4j currently) to provide: 
A shell pipeline for gathering network packets on a given interface and understanding various shapes of the network including scatter plots, DBSCAN outlier, subgraph analysis, and directed network graphs. The name JAWS because "wireshark", and I like the original Jaws movie. Which to me is really anomalies and outliers.
    
RECOMMENDED: Review the README in the repo's root directory for more information on how to setup and use JAWS.

          
If you have all of the accounts, keys, variables, and models, ready to go... 
Then get started by creating a Neo4j database by running the following commands in the root JAWS directory:

docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .
docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms

          
Once the database is running, you can use the following commands to operate the JAWS pipeline:
                    
To begin collecting packets for a specified duration:
neosea --duration 10

This will default to the Ethernet interface and captures database. To change these parameters run:
neosea --interface "Ethernet" --database "captures" --duration 10
          
To add Organization Nodes and Ownership relationships to IP Addresses:
neonet
        
To process packets into embeddings using OpenAI's text-embedding-3-large model or StarCoder2 w/ Quantization:
neotransform --api "openai" or "starcoder"
          
To view cluster plots of the network packets:
neojawsx
          
To generate an analysis of network traffic using OpenAI's gpt-3.5-turbo-16k or Meta-Llama-3-8B-Instruct:
neoharbor --api "openai" or "llama"
          
neosink to clear the database.
          
NOTE: All commands default to the captures database.
          
""")


if __name__ == "__main__":
    main()