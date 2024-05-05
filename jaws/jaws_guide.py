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
    [gray70]Note: JAWS is set to run against the OpenAI API by default and does not require a specific CPU or GPU.[/]
    [gray70]If you plan to run the local models on the host system or in the container, download them first:[/]
    [grey100]jaws-anchor --model 'starcoder', 'llama', or 'all'[/] 
    [gray100]docker exec -it jaws-anchor +/- --model 'starcoder' or 'llama'[/]
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
    [gray100]docker exec -it jaws-compute --api 'transformers'[/]
    """)
   
    print("""
    [gray70]To view cluster plots of embeddings:[/]
    [grey100]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packet' (or 'org') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To generate an 'expert' analysis:[/]
    [grey100]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'transformers/Llama3') --database 'captures'[/]
    [gray100]docker exec -it jaws-advisor --api 'transformers'[/]
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