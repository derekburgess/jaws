from rich import print
from jaws.config import DATABASE, DEFAULT_PACKET_MODEL

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
    K-means, DBSCAN, OpenAI, and local transformers.
    [/]""")
    
    print(f"""[gray100]
    JAWS is set to run against the OpenAI API by default and does not require a specific GPU. JAWS can also
    be configured to run on device using local transformers. If you create the default '{DATABASE}' database,
    and use OpenAI,you can in theory simply run the commands with no additional options.
    [/]""")

    print(f"""[gray100]
    [grey85]JAWS stores data in Neo4j. The 'harbor' Dockerfile deploys a headless Neo4j instance, so you can 
    skip installing the Neo4j Desktop app:[/]
    [turquoise2][DOCKER][/] cd harbor && docker build -t jaws-neodbms
          --build-arg NEO4J_USERNAME
          --build-arg NEO4J_PASSWORD
          --build-arg DEFAULT_DATABASE='{DATABASE}' .
    [turquoise2][DOCKER][/] docker run --name '{DATABASE}' -p 7474:7474 -p 7687:7687 --detach jaws-neodbms
    [grey85]This creates the default '{DATABASE}' database for you. The graph is still browsable at http://localhost:7474 if you want the GUI.[/]
    [/]""")

    print(f"""[gray100]
    [grey85]The 'ocean' Dockerfile deploys the JAWS model container, a CUDA image with JAWS and its dependencies installed, 
    for running local transformers on a GPU:[/]
    [turquoise2][DOCKER][/] cd ocean && docker build -t jaws-image
          --build-arg NEO4J_URI
          --build-arg NEO4J_USERNAME
          --build-arg NEO4J_PASSWORD
          --build-arg IPINFO_API_KEY
          --build-arg OPENAI_API_KEY
          --build-arg HUGGINGFACE_API_KEY .
    [turquoise2][DOCKER][/] docker run --gpus 1 --network host --name jaws-container --detach jaws-image
    [grey85]Once running, the jaws-container commands below (docker exec -it jaws-container ...) operate against this container.[/]
    [/]""")

    print(f"""[gray100]
    [grey85]If you plan to run transformers on device, it is recommended that you download them first:[/]
    [green1][green1][CLI][/][/] jaws-utils --model '{DEFAULT_PACKET_MODEL}' 
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-utils --model '{DEFAULT_PACKET_MODEL}'
    [orange1][WARNING][/] This will download a large amount of data and may take some time.
    [/]""")

    print(f"""[gray100]
    [grey85]To drop the database:[/]
    [green1][CLI][/] jaws-utils [grey50]OPTIONAL[/] --drop '{DATABASE}'
    [orange1][WARNING][/] This will erase all data!
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
    [green1][CLI][/] jaws-compute [grey50]OPTIONAL[/] --api 'openai', 'transformers' --model '{DEFAULT_PACKET_MODEL}' --database '{DATABASE}'
    [turquoise2][DOCKER][/] docker exec -it jaws-container jaws-compute --api 'transformers'
    [grey85]--model selects a local transformers model when --api transformers (see config.PACKET_MODELS).[/]
    [/]""")
   
    print(f"""[gray100]
    [grey85]To view cluster plots of embeddings:[/]
    [green1][CLI][/] jaws-finder [grey50]OPTIONAL[/] --database '{DATABASE}' --components 2 --whiten
    [grey85]You can pass --components to set the number of PCA dimensions to retain for clustering (min 2, default 2).[/]
    [grey85]You can also pass --whiten which scales each PCA component to unit variance. (default: off).[/]
    [/]""")

    print(f"""[gray100]
    [grey85]MCP server:[/]
    [green1][CLI][/] jaws-mcp [grey50]OPTIONAL[/] --host '0.0.0.0' --port 8765 [grey50]OR[/] --stdio
    [grey85]Runs an SSE MCP server (default) so agents such as Claude Code can use JAWS. Pass --stdio for spawn-based clients.[/]
    [/]""")

    print("""[grey50]
    version 2.0.0 BETA, 2026
    https://github.com/derekburgess/jaws
    [/]""")

if __name__ == "__main__":
    main()