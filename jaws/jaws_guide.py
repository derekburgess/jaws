from rich import print


def main():
    print(r"""
    [bold turquoise2]
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
    [/]
    """)

    print("""
    [gray70]First build the dbms container from the /jaws/harbor directory:[/]
    [grey100]docker build -t jaws-neodbms --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD .[/]
    [grey100]docker run --name captures -p 7474:7474 -p 7687:7687 jaws-neodbms[/]
    """)

    print("""
    [gray70]You can skip this step and run JAWS on the host system, or build the JAWS container from the /jaws/ocean directory:[/]
    [grey100]docker build -t jaws-image --build-arg NEO4J_URI --build-arg NEO4J_USERNAME --build-arg NEO4J_PASSWORD --build-arg IPINFO_API_KEY --build-arg OPENAI_API_KEY --build-arg HUGGINGFACE_API_KEY .[/]
    [grey100]docker run --gpus 1 --network host --privileged --publish 5297:5297 --volume PATH:/home --name jaws-container --detach jaws-image[/]
    [grey100]docker exec -it jaws-container bash[/]
    """)

    print("""
    [gray70]Note: JAWS is set to run against the OpenAI API by default and does not require a specific CPU or GPU.[/]
    [gray70]If you plan to run the local models on the host system or in the container, download them first:[/]
    [grey100]jaws-anchor --model 'starcoder', 'llama', or 'all'[/] 
    """)

    print("""
    [gray70]To capture packets:[/]
    [grey100]jaws-capture[/] [gray70]OPTIONAL[/] [grey100]--interface 'Ethernet' --duration 10 --database 'captures'[/]
    """)

    print("""
    [gray70]To import packets from capture files (pcapng):[/]
    [grey100]jaws-import --file PATH [gray70]OPTIONAL[/] --database 'captures'[/]
    """)

    print("""
    [gray70]To investigate IP Addresses:[/]
    [grey100]jaws-ipinfo[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray70]To compute embeddings from packets or organization sets:[/]
    [grey100]jaws-compute[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'transformers/StarCoder2') --type 'packet' (or 'org') --database 'captures'[/]
    """)
   
    print("""
    [gray70]To view cluster plots of embeddings:[/]
    [grey100]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packet' (or 'org') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To generate an 'expert' analysis:[/]
    [grey100]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'transformers/Llama3') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To clear the database:[/]
    [grey100]jaws-clear[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [grey100]version 1.0.0 BETA[/]
    [gray70]https://github.com/derekburgess/jaws[/]
    """)


if __name__ == "__main__":
    main()