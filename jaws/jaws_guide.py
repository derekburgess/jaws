from rich import print


def main():
    print(r"""
    [dark_turquoise]
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
    [/]
    """)

    
    print("""
    [gray100]JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for
    the purpose of identifying outliers. It gathers and stores packets/osint in a graph database (Neo4j).
    It also provides a set of commands to transform and process packets into plots and reports using: 
    K-means, DBSCAN, OpenAI, StarCoder2, and Llama3.[/]
    """)
    
    
    print("""
    [sea_green3]While JAWS is indended to be run locally using open models, is set to run against the OpenAI API by 
    default and does not require a specific GPU. You can in theory simply run the commands with no options.[/]
    """)

    print("""
    [gray100]If you plan to run the models locally, it is recommended that you download them first:[/]
          
    [aquamarine1]jaws-anchor[/] [gray70]OPTIONAL[/] [grey100]--model 'all', 'starcoder, 'llama'[/] 
          
    [turquoise2][Docker][/] [gray100]docker exec -it jaws-compute jaws-anchor[/]
    [gold1][WARNING] This will download a large amount of data and may take some time.[/]
    """)

    print("""
    [gray100]To capture packets:[/]
          
    [aquamarine1]jaws-capture[/] [gray70]OPTIONAL[/] [grey100]--interface 'Ethernet' --duration 10 --database 'captures'[/]
    """)

    print("""
    [gray100]To import packets from capture files (pcapng):[/]
          
    [aquamarine1]jaws-import --file PATH[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray100]To investigate IP addresses and build organization nodes:[/]
          
    [aquamarine1]jaws-ipinfo[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray100]To compute embeddings from packets or organization sets:[/]
          
    [aquamarine1]jaws-compute[/] [gray70]OPTIONAL[/] [grey100]--api 'openai', 'transformers' --type 'packet', 'org' --database 'captures'[/]
          
    [turquoise2][Docker][/] [gray100]docker exec -it jaws-compute jaws-compute --api 'transformers'[/]
    """)
   
    print("""
    [gray100]To view cluster plots of embeddings and build outlier nodes:[/]
          
    [aquamarine1]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packet', 'org' --database 'captures'[/]
    """)
          
    print("""
    [gray100]To generate an 'expert' analysis from outliers:[/]
          
    [aquamarine1]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'openai', 'transformers' --database 'captures'[/]
    [gray100]docker exec -it jaws-compute jaws-advisor --api 'transformers'[/]
    """)
          
    print("""
    [gray100]To clear the database:[/]
          
    [aquamarine1]jaws-clear[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    [gold1][WARNING] This will erase all data![/]
    """)

    print("""
    [grey100]version 1.0.0 BETA[/]
    [gray70]https://github.com/derekburgess/jaws[/]
    """)


if __name__ == "__main__":
    main()