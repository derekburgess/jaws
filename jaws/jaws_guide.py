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
    [indian_red]RECOMMENDED: Review the README for more information on setup and usage.[/]
    """)

    print("""
    [gray70]From the JAWS root directory:[/]
    [grey100]docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .[/]
    [grey100]docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms[/]
    """)

    print("""
    [gray70]To collect packets:[/]
    [grey100]jaws-capture[/] [gray70]OPTIONAL[/] [grey100]--interface 'Ethernet' --duration 10 --database 'captures'[/]
    """)

    print("""
    [gray70]To import packets from capture files (pcapng):[/]
    [grey100]jaws-import --file PATH/TO/FILE [gray70]OPTIONAL[/] --database 'captures'[/]
    """)

    print("""
    [gray70]To investigate IP Addresses:[/]
    [grey100]jaws-ipinfo[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray70]To process packets or organization sets into embeddings:[/]
    [grey100]jaws-compute[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'starcoder') --type 'packet' (or 'org') --database 'captures'[/]
    """)
   
    print("""
    [gray70]To view cluster plots of embeddings:[/]
    [grey100]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packet' (or 'org') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To generate an 'expert' analysis:[/]
    [grey100]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'openai' (or 'llama') --database 'captures'[/]
    """)
          
    print("""
    [gray70]To clear the database:[/]
    [grey100]jaws-clear[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)


if __name__ == "__main__":
    main()