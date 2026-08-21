"""Print the supported JAWS 3.0 command-line workflows."""

from __future__ import annotations

from rich import print

from jaws.settings import DEFAULT_DATABASE, DEFAULT_PACKET_MODEL

PACKAGE_VERSION = "3.0.0"


def main() -> None:
    print(
        r"""[turquoise2]
        o   O   o       o  o-o
        |  / \  |       | |
        | o---o o   o   o  o-o
    \   o |   |  \ / \ /      |
     o-o  o   o   o   o   o--o
        [/]

[bold]JAWS is a command-line research workbench for network-behavior analysis.[/]
It captures or imports traffic, enriches and profiles endpoints, ranks unusual behavior,
and preserves reproducible experiments whose findings can be traced back to evidence.
An anomaly is an invitation to investigate, not an automatic threat verdict.
"""
    )

    print(
        f"""[bold]1. Configure the services you use[/]

  [bold green]ENV[/] NEO4J_URI=bolt://localhost:7687
  [bold green]ENV[/] NEO4J_USERNAME=neo4j
  [bold green]ENV[/] NEO4J_PASSWORD=choose-a-password
  [dim]Optional: IPINFO_API_KEY for enrichment; OPENAI_API_KEY for OpenAI embeddings.[/]

[bold]2. Start the reproducible runtime, if desired[/]

  [bold cyan]DOCKER[/] docker compose -f compose.dev.yml up --build --detach --wait

The base profile starts pinned Neo4j, schema migration, CPU analysis, and MCP services.
Use compose.gpu.yml only for NVIDIA analysis and compose.edge.yml only for the privileged
capture sensor. Run the CLI directly from the installed Python environment. Neo4j Browser
is available at http://127.0.0.1:7474; JAWS does not ship a web admin dashboard.

[bold]3. Capture, profile, and rank[/]

  [bold green]CLI[/] jaws-capture --list
  [bold green]CLI[/] jaws-capture --interface eth0 --duration 60
  [dim]or: jaws-capture --file /path/to/capture.pcap --local-ip 192.0.2.10[/]
  [bold green]CLI[/] jaws-ipinfo
  [bold green]CLI[/] jaws-compute --api openai --session latest
  [bold green]CLI[/] jaws-finder --session latest

Captures accumulate. Do not erase the database between ordinary sessions: endpoint history
is the reference used to distinguish persistent oddness from meaningful change. The default
database is {DEFAULT_DATABASE!r}.

[bold]4. Use local or numeric representations[/]

  [bold green]CLI[/] jaws-utils --model {DEFAULT_PACKET_MODEL}
  [bold green]CLI[/] jaws-compute --api transformers --model {DEFAULT_PACKET_MODEL} --session latest
  [bold green]CLI[/] jaws-compute --api numeric --session latest

Public local models require no provider key. Downloads may be large. Numeric mode needs no
embedding provider.

[bold]5. Run a reproducible research study[/]

  [bold green]CLI[/] jaws-research validate examples/research/control-treatment.json \\
        --catalog examples/research/catalog.json
  [bold green]CLI[/] jaws-research run examples/research/control-treatment.json \\
        --catalog examples/research/catalog.json --root .jaws-research --json
  [bold green]CLI[/] jaws-research verify PATH_FROM_RUN_RESULT --json

[bold]6. Evaluate JAWS or exercise the optional agent laboratory[/]

  [bold green]CLI[/] jaws-benchmark --tier smoke --output-dir benchmark-v1-smoke
  [bold green]CLI[/] jaws-benchmark --tier full --output-dir benchmark-v1-full
  [bold green]CLI[/] jaws-lab identity examples/research/control-treatment.json

Benchmarks compare rankers and emit JSON, Markdown, static HTML, and checksummed bundles.
The lab is an experimental, approval-gated OHEO collaborator; it is not a detector or UI.

[bold]7. Perform guarded human administration[/]

  [bold green]CLI[/] jaws-admin plan --database {DEFAULT_DATABASE}
  [bold green]CLI[/] jaws-admin erase --database {DEFAULT_DATABASE} --confirm 'EXACT STRING FROM PLAN'
  [bold green]CLI[/] jaws-admin audit --database {DEFAULT_DATABASE}

Destructive administration has no default target and is not available to MCP or the lab.

[bold]8. Expose the research services through MCP[/]

  [bold green]CLI[/] jaws-mcp --stdio
  [bold green]CLI[/] jaws-mcp --http --host 127.0.0.1 --port 8765

The default network transport is Streamable HTTP. MCP exposes bounded research operations,
not live capture, arbitrary shell/Cypher, host paths, credentials, or database erasure.

[dim]JAWS {PACKAGE_VERSION} · https://github.com/derekburgess/jaws[/]
"""
    )


if __name__ == "__main__":
    main()
