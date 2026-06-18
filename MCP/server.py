"""JAWS MCP Server — exposes the full JAWS network-analysis pipeline via FastMCP."""

from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os
import json
from pathlib import Path

from jaws.config import DATABASE, get_neo4j_driver

ROOT = Path(__file__).parent.parent   # /path/to/jaws/
SCRIPTS = ROOT / "jaws"

# No hard-coded wall-clock timeout. Capture is bounded by `duration`, and compute /
# anomaly detection are bounded by the dataset, so the scripts self-terminate — an
# arbitrary server-side number would only ever be wrong for someone's hardware. The
# real limit is the MCP client's own per-tool-call timeout (e.g. Claude Code's
# MCP_TOOL_TIMEOUT). An operator who wants a server-side backstop can set
# JAWS_MCP_TIMEOUT (seconds); by default there is none.
_env_timeout = os.environ.get("JAWS_MCP_TIMEOUT")
TIMEOUT = int(_env_timeout) if _env_timeout else None

INSTRUCTIONS = """JAWS captures network traffic into a Neo4j graph, enriches it with OSINT, embeds it, and flags anomalies.

The tools form a linear pipeline — run them in order:
  1. list_interfaces      — choose a physical interface (virtual/loopback are filtered out).
  2. capture_packets      — sniff that interface for N seconds into the graph.
  3. document_organizations — enrich the captured IPs with org/ASN ownership.
  4. compute_embeddings   — aggregate each IP's traffic into an endpoint profile and embed it.
  5. anomaly_detection    — cluster the endpoint (per-IP) embeddings (PCA + DBSCAN) and flag outliers.

The unit of analysis is the IP address (labeled with its organization): each IP becomes one
endpoint profile describing its outbound and inbound traffic, and outliers are anomalous IPs —
including unusual outbound traffic from the local host.

Anytime:
  - fetch_traffic   — read the per-IP endpoint profiles back from the graph.
  - drop_database   — wipe the graph (typically before a fresh capture session).

Notes:
  - Keep captures short (30-120s); capture again rather than running one long session.
  - After every capture, run document_organizations and compute_embeddings before anomaly_detection.
  - Use compute_embeddings(api='transformers') on a GPU host; otherwise api='openai'. The local
    transformer model must be downloaded on the host beforehand (`jaws-utils --model ...`); this is
    a one-time setup step done outside the MCP.
  - compute_embeddings and anomaly_detection can run for a while on large captures — if your client
    aborts them early, raise its per-tool-call timeout (e.g. Claude Code's MCP_TOOL_TIMEOUT).
  - All tools operate on the single '%s' database.""" % DATABASE

mcp = FastMCP("JAWS - Wireshark MCP with Network Analysis Tools", instructions=INSTRUCTIONS)


def _run(args: list[str], timeout: int | None = TIMEOUT) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return f"ERROR: process exceeded the JAWS_MCP_TIMEOUT backstop of {timeout}s"
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        # Prefer stderr (tracebacks / argparse errors); fall back to stdout.
        return f"ERROR (exit {result.returncode}): {err or out or 'no output'}"
    return out or "(no output)"


def _script(name: str, *args: str) -> str:
    return _run([sys.executable, str(SCRIPTS / name), *args])


@mcp.tool(name="list_interfaces", description=(
    "Step 1. List the physical network interfaces available for capture, one per line. "
    "Virtual/loopback interfaces (lo, docker, tailscale) are already filtered out. "
    "Pick one of these names to pass to capture_packets."
))
def list_interfaces() -> str:
    return _script("jaws_capture.py", "--list")


@mcp.tool(name="capture_packets", description=(
    "Step 2. Capture live packets from an interface into the graph for `duration` seconds. "
    "Use an interface name from list_interfaces. Keep captures short (30-120s) and capture "
    "again rather than running one long session. The call runs for roughly `duration` seconds."
))
def capture_packets(interface: str, duration: int = 60) -> str:
    return _script(
        "jaws_capture.py",
        "--interface", interface,
        "--duration", str(duration),
    )


@mcp.tool(name="document_organizations", description=(
    "Step 3. Enrich the captured IP addresses with organization/ASN ownership via Ipinfo. "
    "Run after each capture and before compute_embeddings."
))
def document_organizations() -> str:
    return _script("jaws_ipinfo.py")


@mcp.tool(name="compute_embeddings", description=(
    "Step 4. Aggregate each IP's captured traffic (both directions) into an endpoint profile and embed "
    "that profile — one vector per IP — for downstream clustering. "
    "Use api='transformers' (default) on a GPU host — the local model produces tighter clusters and "
    "surfaces anomalies that OpenAI embeddings miss (the model must be pre-downloaded on the host). "
    "Use api='openai' as a fallback when no GPU is available. May run for a while on large captures."
))
def compute_embeddings(api: str = "transformers") -> str:
    return _script("jaws_compute.py", "--api", api)


@mcp.tool(name="anomaly_detection", description=(
    "Step 5. Cluster the per-IP endpoint embeddings with PCA + DBSCAN and flag outlier IPs. Returns a "
    "JSON summary: endpoints_clustered, outliers_flagged, the DBSCAN params (eps/min_samples/components), "
    "and the list of outlier endpoints (an empty list means zero outliers were found, not a failure). "
    "`components` is the number of PCA dimensions to retain (minimum 2). `whiten` scales each PCA "
    "component to unit variance — helps with a few strong components but amplifies noise when many are retained. "
    "`eps` overrides the DBSCAN epsilon; when omitted it is auto-recommended, but that recommendation "
    "tends to overshoot on small captures and return 0 outliers — if outliers_flagged is 0 and you "
    "expected some, re-run with a smaller eps (e.g. 50-70% of the eps shown in the result)."
))
def anomaly_detection(components: int = 2, whiten: bool = False, eps: float | None = None) -> str:
    args = ["--components", str(components)]
    if whiten:
        args.append("--whiten")
    if eps is not None:
        args += ["--eps", str(eps)]
    return _script("jaws_finder.py", *args)


@mcp.tool(name="drop_database", description=(
    "Wipe ALL data from the graph. Irreversible. Typically run before starting a fresh capture session."
))
def drop_database() -> str:
    return _script("jaws_utils.py")


_FETCH_QUERY = """
MATCH (endpoint:ENDPOINT)
WHERE endpoint.TIMESTAMP > datetime() - duration({minutes: $duration})
OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: endpoint.IP_ADDRESS})<-[:OWNERSHIP]-(org:ORGANIZATION)
RETURN DISTINCT
    endpoint.IP_ADDRESS AS ip_address,
    COALESCE(endpoint.ORGANIZATION, org.ORGANIZATION) AS org,
    COALESCE(endpoint.HOSTNAME, ip.HOSTNAME) AS hostname,
    COALESCE(endpoint.LOCATION, ip.LOCATION) AS location,
    endpoint.BYTES_OUT AS bytes_out,
    endpoint.PACKETS_OUT AS packets_out,
    endpoint.OUT_PEERS AS out_peers,
    endpoint.OUT_PORTS AS out_ports,
    endpoint.BYTES_IN AS bytes_in,
    endpoint.PACKETS_IN AS packets_in,
    endpoint.IN_PEERS AS in_peers,
    endpoint.IN_PORTS AS in_ports,
    endpoint.PROTOCOLS AS protocols,
    endpoint.OUTLIER AS outlier,
    endpoint.TIMESTAMP AS timestamp
ORDER BY endpoint.TIMESTAMP DESC
LIMIT $limit
"""


@mcp.tool(name="fetch_traffic", description=(
    "Read processed per-IP endpoint profiles back from the graph as JSON. Each row is one IP with its "
    "org/hostname/location and directional traffic (bytes/packets/peers/ports, outbound and inbound), "
    "plus its outlier flag. `duration` is how many minutes of history to include; `limit` caps the rows. "
    "Most recent first."
))
def fetch_traffic(duration: int = 60, limit: int = 100) -> str:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            result = session.run(_FETCH_QUERY, duration=duration, limit=limit)
            data = [record.data() for record in result]
    except Exception as e:
        return f"ERROR: could not fetch endpoints ({e})"
    if not data:
        return f"(no endpoint profiles found in the last {duration} minutes)"
    # default=str renders Neo4j DateTime values; the rest are JSON-native.
    return json.dumps(data, default=str, indent=2)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sse", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.sse:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
