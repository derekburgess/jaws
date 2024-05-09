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
    [gray70]JAWS is set to run against the OpenAI API by default and does not require a specific GPU.[/]
    [gray70]If you plan to run the Hugging Face models locally, on the host system or in the container, you can download them first using:[/]
    [grey100]jaws-anchor[/] [gray70]OPTIONAL[/] [grey100]--model 'all' (or 'starcoder/llama')[/] 
    [gray100]docker exec -it jaws-compute jaws-anchor[/] [gray70]OPTIONAL[/] [grey100]--model 'all' (or 'starcoder/llama')[/]
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
    [gray70]To investigate IP Addresses and build Organization nodes:[/]
    [grey100]jaws-ipinfo[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray70]To compute embeddings from packets or organization sets:[/]
    [grey100]jaws-compute[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'transformers') --type 'packet' (or 'org') --database 'captures'[/]
    [gray100]docker exec -it jaws-compute jaws-compute --api 'transformers' --type 'packet' (or 'org')[/]
    """)
   
    print("""
    [gray70]To view cluster plots of embeddings:[/]
    [grey100]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packet' (or 'org') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To generate an 'expert' analysis:[/]
    [grey100]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'transformers') --database 'captures'[/]
    [gray100]docker exec -it jaws-compute jaws-advisor --api 'transformers'[/]
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