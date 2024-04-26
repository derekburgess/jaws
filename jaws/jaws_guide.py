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
    [indian_red]RECOMMENDED: The README contains more information on setup and usage.[/]
    """)

    print("""
    [grey58]From the JAWS root directory:[/]
    [grey100]docker build --build-arg LOCAL_NEO4J_USERNAME --build-arg LOCAL_NEO4J_PASSWORD -t neojawsdbms .[/]
    [grey100]docker run --name captures -p 7474:7474 -p 7687:7687 neojawsdbms[/]
    """)

    print("""
    [grey58]To collect packets:[/]
    [grey100]jaws-capture[/] [grey58]OPTIONAL[/] [grey100]--interface 'Ethernet' --database 'captures' --duration 10[/]
    """)

    print("""
    [grey58]To investigate IP Addresses:[/]
    [grey100]jaws-ipinfo[/] [grey58]OPTIONAL[/] [grey100]--database 'captures'[/]
    """)

    print("""
    [grey58]To process packets or orgs into embeddings:[/]
    [grey100]jaws-embedd[/] [grey58]OPTIONAL[/] [grey100]--api 'starcoder' (or openai) --type 'packets' (or orgs) --database 'captures'[/]
    """)
          
    print("""
    [grey58]To view cluster plots of packet embeddings:[/]
    [grey100]jaws-finder[/] [grey58]OPTIONAL[/] [grey100]--type 'packets' (or orgs)[/]
    """)
          
    print("""
    [grey58]To generate an analysis:[/]
    [grey100]jaws-advisor[/] [grey58]OPTIONAL[/] [grey100]--api 'llama' (or openai)[/]
    """)
          
    print("""
    [grey100]jaws-clear[/] [grey58]to clear the database.
    [/]""")

    print("""[navajo_white1]                     
    NOTE:
    - All commands default to the captures database.
    - JAWS performs best locally when neosea is run between 5-30 seconds.
    """)   


if __name__ == "__main__":
    main()