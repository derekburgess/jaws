from rich import print


def main():
    print("""[bold turquoise2]
      JJJJ     A     W   W  SSSS
        J     A A    W   W S
        J    AAAAA   W W W  SSS
    J   J   A     A  W W W     S
      JJJ   A     A   W W   SSSS
    [/bold turquoise2]""")

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
    [grey100]jaws-capture[/] [gray70]OPTIONAL[/] [grey100]--interface 'Ethernet' --database 'captures' --duration 10[/]
    """)

    print("""
    [gray70]To investigate IP Addresses:[/]
    [grey100]jaws-ipinfo[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [gray70]To process packets or orgs into embeddings:[/]
    [grey100]jaws-embedd[/] [gray70]OPTIONAL[/] [grey100]--api 'starcoder' (or openai) --type 'packets' (or orgs) --database 'captures'[/]
    """)
   
    print("""
    [gray70]To view cluster plots of packet embeddings:[/]
    [grey100]jaws-finder[/] [gray70]OPTIONAL[/] [grey100]--type 'packets' (or orgs) --database 'captures'[/]
    """)
          
    print("""
    [gray70]To generate an analysis:[/]
    [grey100]jaws-advisor[/] [gray70]OPTIONAL[/] [grey100]--api 'llama' (or openai) --database 'captures'[/]
    """)
          
    print("""
    [gray70]To clear the database:[/]
    [grey100]jaws-clear[/] [gray70]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)


if __name__ == "__main__":
    main()