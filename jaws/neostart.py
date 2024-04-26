def main():
    print("""
   JJJJ     A     W   W  SSSS
     J     A A    W   W S
     J    AAAAA   W W W  SSS
 J   J   A     A  W W W     S
  JJJ   A     A   W W   SSSS

    
RECOMMENDED: Review the README in the repo's root directory for more information on how to setup and use JAWS.

                  
If you have all of the accounts, keys, variables, and models, ready to go... 
Then get started by creating a Neo4j database by running the following commands in the root JAWS directory:

docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .     
docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms

          
Once the database is running, you can use the following commands to operate the JAWS pipeline:
                    
To begin collecting packets for a specified duration:
neosea --duration 10 >

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

                         
NOTE:
- All commands default to the captures database.
- JAWS performs best locally when neosea is run between 5-30 seconds.
          
""")


if __name__ == "__main__":
    main()