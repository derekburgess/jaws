from rich import print
from jaws.jaws_config import *

def main():
    print(r"""[turquoise2]
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
    [/]""")

    print(f"""[gray100]
    JAWS is a Python based shell pipeline for analyzing the shape and activity of networks for
    the purpose of identifying outliers. It gathers and stores packets/osint in a graph database (Neo4j).
    It also provides a set of commands to transform and process packets into plots and reports using: 
    K-means, DBSCAN, OpenAI, '{PACKET_MODEL_ID}', and '{LANG_MODEL_ID}'.
    [/]""")
    
    print("""[gray100]
    JAWS is set to run against the OpenAI API by default and does not require a specific GPU. JAWS can
    also be configured to run locally using 'open' models. If you create the default captures database, 
    you can in theory simply run the commands with no options.
    [/]""")

    print(f"""[gray100]
    [grey85]If you plan to run the models locally, it is recommended that you download them first:[/]
    [green1][green1][CLI][/][/] jaws-anchor [grey50]OPTIONAL[/] --model '{PACKET_MODEL_ID}', '{LANG_MODEL_ID}' 
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-anchor
    [orange1][WARNING][/] This will download a large amount of data and may take some time.
    [/]""")

    print(f"""[gray100]
    [grey85]To capture or import packets:[/]
    [green1][CLI][/] jaws-capture [grey50]OPTIONAL[/] --interface 'Ethernet' OR --file PATH --duration 10 --database '{DATABASE}'
    [grey85]You can use jaws-capture --list to list available interfaces.[/]
    [/]""")

    print(f"""[gray100]
    [grey85]To investigate IP addresses and build organization nodes:[/]
    [green1][CLI][/] jaws-ipinfo [grey50]OPTIONAL[/] --database '{DATABASE}'
    [/]""")

    print(f"""[gray100]
    [grey85]To compute embeddings:[/]
    [green1][CLI][/] jaws-compute [grey50]OPTIONAL[/] --api 'openai', 'transformers' --database '{DATABASE}'
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-compute --api 'transformers'
    [/]""")
   
    print(f"""[gray100]
    [grey85]To view cluster plots of embeddings:[/]
    [green1][CLI][/] jaws-finder [grey50]OPTIONAL[/] --database '{DATABASE}'
    [/]""")
          
    print(f"""[gray100]
    [grey85]To generate an 'expert' analysis:[/] 
    [green1][CLI][/] jaws-advisor [grey50]OPTIONAL[/] --api 'openai', 'transformers' --database '{DATABASE}'
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-advisor --api 'transformers'
    [/]""")
          
    print(f"""[gray100]
    [grey85]To clear the database:[/]
    [green1][CLI][/] jaws-clear [grey50]OPTIONAL[/] --database '{DATABASE}'
    [orange1][WARNING][/] This will erase all data!
    [/]""")

    print("""[grey50]
    version 1.5.0 BETA, 2025
    https://github.com/derekburgess/jaws
    [/]""")

if __name__ == "__main__":
    main()