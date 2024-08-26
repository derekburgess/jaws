from rich import print

def main():
    print(r"""[turquoise2]
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
    [/]""")


    print("""[gray100]
    JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for
    the purpose of identifying outliers. It gathers and stores packets/osint in a graph database (Neo4j).
    It also provides a set of commands to transform and process packets into plots and reports using: 
    K-means, DBSCAN, OpenAI, StarCoder2, and Llama3.
    [/]""")
    
    
    print("""[gray100]
    While JAWS is intended to run locally using open models, but is set to run against the OpenAI API by 
    default, and does not require a specific GPU. You can in theory simply run the commands with no options.
    [/]""")

    print("""[gray100]
    [grey85]If you plan to run the models locally, it is recommended that you download them first:[/]
    [green1][green1][CLI][/][/] jaws-anchor [grey50]OPTIONAL[/] --model 'all', 'starcoder, 'llama' 
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-anchor
    [orange1][WARNING][/] This will download a large amount of data and may take some time.
    [/]""")

    print("""[gray100]
    [grey85]To capture or import packets:[/]
    [green1][CLI][/] jaws-capture [grey50]OPTIONAL[/] --interface 'Ethernet' --file PATH --duration 10 --database 'captures'
    [/]""")

    print("""[gray100]
    [grey85]To investigate IP addresses and build organization nodes:[/]
    [green1][CLI][/] jaws-ipinfo [grey50]OPTIONAL[/] --database 'captures'
    [/]""")

    print("""[gray100]
    [grey85]To compute embeddings from packets or organization sets:[/]
    [green1][CLI][/] jaws-compute [grey50]OPTIONAL[/] --api 'openai', 'transformers' --database 'captures'
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-compute --api 'transformers'
    [/]""")
   
    print("""[gray100]
    [grey85]To view cluster plots of embeddings and build outlier nodes:[/]
    [green1][CLI][/] jaws-finder [grey50]OPTIONAL[/] --database 'captures'
    [/]""")
          
    print("""[gray100]
    [grey85]To generate an 'expert' analysis from outliers:[/] 
    [green1][CLI][/] jaws-advisor [grey50]OPTIONAL[/] --api 'openai', 'transformers' --database 'captures'  
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-advisor --api 'transformers'
    [/]""")
          
    print("""[gray100]
    [grey85]To clear the database:[/]
    [green1][CLI][/] jaws-clear [grey50]OPTIONAL[/] --database 'captures'
    [orange1][WARNING][/] This will erase all data!
    [/]""")

    print("""[grey50]
    version 1.0.0 BETA, 2024
    https://github.com/derekburgess/jaws
    [/]""")


if __name__ == "__main__":
    main()