def main():
    print(r"""
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
    
    """)

    
    print("""
    JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for
    the purpose of identifying outliers. It gathers and stores packets/osint in a graph database (Neo4j).
    It also provides a set of commands to transform and process packets into plots and reports using: 
    K-means, DBSCAN, OpenAI, StarCoder2, and Llama3.
    """)
    
    
    print("""
    While JAWS is intended to run locally using open models, but is set to run against the OpenAI API by 
    default, and does not require a specific GPU. You can in theory simply run the commands with no options.
    """)

    print("""
    If you plan to run the models locally, it is recommended that you download them first:
          
    [CLI] jaws-anchor OPTIONAL --model 'all', 'starcoder, 'llama' 
          
    [DOCKER] docker exec -it jaws-container jaws-anchor
          
    [WARNING] This will download a large amount of data and may take some time.
    """)

    print("""
    To capture packets:
          
    [CLI] jaws-capture OPTIONAL --interface 'Ethernet' --duration 10 --database 'captures'
    """)

    print("""
    To import packets from a capture file (pcapng):
          
    [CLI] jaws-import --file PATH OPTIONAL --database 'captures'
    """)

    print("""
    To investigate IP addresses and build organization nodes:
          
    [CLI] jaws-ipinfo OPTIONAL --database 'captures'
    """)

    print("""
    To compute embeddings from packets or organization sets:
          
    [CLI] jaws-compute OPTIONAL --api 'openai', 'transformers' --type 'packet', 'org' --database 'captures'
          
    [DOCKER] docker exec -it jaws-container jaws-compute --api 'transformers'
    """)
   
    print("""
    To view cluster plots of embeddings and build outlier nodes:
          
    [CLI] jaws-finder OPTIONAL --type 'packet', 'org' --database 'captures'
    """)
          
    print("""
    To generate an 'expert' analysis from outliers:
          
    [CLI] jaws-advisor OPTIONAL --api 'openai', 'transformers' --database 'captures'
          
    [DOCKER] docker exec -it jaws-container jaws-advisor --api 'transformers'
    """)
          
    print("""
    To clear the database:
          
    [CLI] jaws-clear OPTIONAL --database 'captures'
          
    [WARNING] This will erase all data!
    """)

    print("""
    version 1.0.0 BETA
    https://github.com/derekburgess/jaws
    """)


if __name__ == "__main__":
    main()