"""JAWS MCP Server — exposes the full JAWS network-analysis pipeline via FastMCP."""

from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os
import json
from pathlib import Path
from typing import Any

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
  - fetch_traffic   — read the per-IP endpoint profiles back from the graph (windowed overview).
  - inspect_endpoint — drill into ONE IP (e.g. an outlier): its profile, who it talked to (peers),
                       and a raw packet sample. The join key from an anomaly back to its detail.
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


def _try_json(text: str) -> Any:
    """json.loads that returns None instead of raising on non-JSON input."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _run(args: list[str], timeout: int | None = TIMEOUT) -> dict[str, Any]:
    """Run a JAWS CLI and return its result as a native dict.

    In agent mode (the subprocess inherits a non-TTY stdout) every CLI prints
    exactly one JSON document on stdout via the Reporter, already wrapped in the
    {"ok": bool, ...} envelope — ok=True merged with the result fields on success,
    {"ok": false, "error": ...} on failure. We parse that here and return the dict
    so the MCP payload arrives already-structured: returning a `dict[str, Any]`
    makes FastMCP emit it as structuredContent verbatim, instead of wrapping a JSON
    string inside another JSON string ({"result": "{...}"}, the double-encoding the
    client otherwise has to parse twice).

    The contract is uniform: every dict returned here carries a boolean `ok`, so a
    client branches on that one field deterministically — never string-matching
    prose or testing for the presence of an "error" key. Subprocess-level outcomes
    the Reporter never sees (timeout, a crash before any JSON is printed, non-JSON
    stdout) are stamped with ok=False here so they conform to the same shape.
    """
    try:
        result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"process exceeded the JAWS_MCP_TIMEOUT backstop of {timeout}s"}
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    parsed = _try_json(out)
    if result.returncode != 0:
        # A non-zero exit may still carry the structured envelope on stdout (a
        # reporter.error already stamped ok=False); prefer it, else synthesize one
        # from stderr (tracebacks / argparse) or stdout.
        if isinstance(parsed, dict):
            parsed.setdefault("ok", False)
            return parsed
        return {"ok": False, "error": err or out or "no output", "exit_code": result.returncode}
    if isinstance(parsed, dict):
        # Already enveloped by the Reporter; backstop ok in case a CLI printed a
        # bare dict outside reporter.result.
        parsed.setdefault("ok", True)
        return parsed
    if parsed is not None:
        # Valid JSON that isn't an object (e.g. a bare array) — keep it addressable.
        return {"ok": True, "result": parsed}
    if not out:
        return {"ok": False, "error": "process produced no output"}
    # Unexpected non-JSON on stdout in agent mode — a contract violation. Preserve
    # it rather than crash, but mark it failed so the client doesn't read it as a result.
    return {"ok": False, "error": "process produced non-JSON output", "raw": out}


def _script(name: str, *args: str) -> dict[str, Any]:
    return _run([sys.executable, str(SCRIPTS / name), *args])


@mcp.tool(name="list_interfaces", description=(
    "Step 1. List the physical network interfaces available for capture, one per line. "
    "Virtual/loopback interfaces (lo, docker, tailscale) are already filtered out. "
    "Pick one of these names to pass to capture_packets."
))
def list_interfaces() -> dict[str, Any]:
    return _script("jaws_capture.py", "--list")


@mcp.tool(name="capture_packets", description=(
    "Step 2. Capture live packets from an interface into the graph for `duration` seconds. "
    "Use an interface name from list_interfaces. Keep captures short (30-120s) and capture "
    "again rather than running one long session. The call runs for roughly `duration` seconds."
))
def capture_packets(interface: str, duration: int = 60) -> dict[str, Any]:
    return _script(
        "jaws_capture.py",
        "--interface", interface,
        "--duration", str(duration),
    )


@mcp.tool(name="document_organizations", description=(
    "Step 3. Enrich the captured IP addresses with organization/ASN ownership via Ipinfo. "
    "Run after each capture and before compute_embeddings."
))
def document_organizations() -> dict[str, Any]:
    return _script("jaws_ipinfo.py")


@mcp.tool(name="compute_embeddings", description=(
    "Step 4. Aggregate each IP's captured traffic (both directions) into an endpoint profile and embed "
    "that profile — one vector per IP — for downstream clustering. "
    "Use api='transformers' (default) on a GPU host — the local model produces tighter clusters and "
    "surfaces anomalies that OpenAI embeddings miss (the model must be pre-downloaded on the host). "
    "Use api='openai' as a fallback when no GPU is available. May run for a while on large captures."
))
def compute_embeddings(api: str = "transformers") -> dict[str, Any]:
    return _script("jaws_compute.py", "--api", api)


@mcp.tool(name="anomaly_detection", description=(
    "Step 5. Cluster the per-IP endpoint embeddings with PCA + DBSCAN and score every IP for anomaly. "
    "Returns a JSON summary: endpoints_clustered, outliers_flagged, the DBSCAN params "
    "(eps/min_samples/components), a `units` map labeling the raw numbers, and `endpoints` — the FULL "
    "list of clustered IPs sorted by `anomaly_score` (descending), so there is always a ranking to "
    "triage even when DBSCAN flags nothing. Each endpoint carries `anomaly_score` (L2 norm of its "
    "per-feature robust-z — overall behavioral distance from the typical host), `is_outlier` (the DBSCAN "
    "verdict), and `reasons` — the features that made it stand out, each with its value, unit, robust_z, "
    "direction (high/low), and a `host_relative` gloss naming the direction relative to the capture host. "
    "Counts are from each endpoint's OWN perspective and scored endpoints are REMOTE (host excluded by "
    "default), so a remote IP's high bytes_out is traffic it sent TO the host (a host DOWNLOAD), NOT exfil; "
    "the outbound-from-host signal is its bytes_in. Read `host_relative` before labeling a finding — that "
    "lets you tell a host-upload/exfil from a download from a low-interval_cv beacon. The top-level "
    "`perspective` field restates this. outliers_flagged == 0 is not a failure. "
    "The result also carries a `host_outbound` section — the defender-frame counterpart to `endpoints`: "
    "the CAPTURE HOST's own outbound, per destination, isolated from raw packets (host as source) and "
    "ranked by `outbound_score` on the host-upload distribution, with `upload_download_ratio` high = "
    "exfil-shaped. The host is excluded from clustering (a hub), so its real outbound is demoted in the "
    "`endpoints` ranking; use `host_outbound.destinations` to judge host exfiltration or beaconing. "
    "`components` is the number of PCA dimensions to retain (minimum 2). `whiten` scales each PCA "
    "component to unit variance — helps with a few strong components but amplifies noise when many are retained. "
    "`eps` overrides the DBSCAN epsilon; when omitted it is auto-recommended, but that recommendation "
    "tends to overshoot on small captures and return 0 outliers — if outliers_flagged is 0 and you "
    "expected some, re-run with a smaller eps (e.g. 50-70% of the eps shown in the result). "
    "`feature_weight` controls how much each endpoint's behavioral numbers (bytes/packets/peers, in & "
    "out) drive clustering vs. the text profile: 0 clusters on text/org/protocol only, higher (default "
    "1.0) surfaces volume/fan-out anomalies like unusual outbound traffic. "
    "The capture host itself is excluded by default (it is a structural hub that dominates clustering; "
    "its outbound traffic still appears as remote endpoints' inbound) — set include_local=true to keep it."
))
def anomaly_detection(components: int = 2, whiten: bool = False, eps: float | None = None, feature_weight: float = 1.0, include_local: bool = False) -> dict[str, Any]:
    args = ["--components", str(components), "--feature-weight", str(feature_weight)]
    if whiten:
        args.append("--whiten")
    if eps is not None:
        args += ["--eps", str(eps)]
    if include_local:
        args.append("--include-local")
    return _script("jaws_finder.py", *args)


@mcp.tool(name="drop_database", description=(
    "Wipe ALL data from the graph. Irreversible. Typically run before starting a fresh capture session."
))
def drop_database() -> dict[str, Any]:
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
    endpoint.INTERVAL_MEAN AS interval_mean,
    endpoint.INTERVAL_CV AS interval_cv,
    endpoint.OUTLIER AS outlier,
    endpoint.TIMESTAMP AS timestamp
ORDER BY endpoint.TIMESTAMP DESC
LIMIT $limit
"""


@mcp.tool(name="fetch_traffic", description=(
    "Read processed per-IP endpoint profiles back from the graph. Returns an object with an `endpoints` "
    "list (most recent first) and a `count`; each endpoint is one IP with its org/hostname/location and "
    "directional traffic (bytes/packets/peers/ports, outbound and inbound), plus its outlier flag. "
    "Directions are from the endpoint's OWN perspective: for a remote IP, `bytes_out` is what it sent TO "
    "the capture host (a host download), and `bytes_in` is what the host sent to it (outbound from host). "
    "`duration` is how many minutes of history to include; `limit` caps the rows. "
    "This is the windowed overview; to drill into ONE specific IP (e.g. an outlier from anomaly_detection) "
    "and see exactly who it talked to, use inspect_endpoint instead."
))
def fetch_traffic(duration: int = 60, limit: int = 100) -> dict[str, Any]:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            result = session.run(_FETCH_QUERY, duration=duration, limit=limit)
            data = [record.data() for record in result]
    except Exception as e:
        return {"ok": False, "error": f"could not fetch endpoints ({e})"}
    # Round-trip through json with default=str to coerce Neo4j DateTime values into
    # JSON-native strings, so FastMCP can serialize the returned dict cleanly.
    endpoints = json.loads(json.dumps(data, default=str))
    return {"ok": True, "endpoints": endpoints, "count": len(endpoints), "duration_minutes": duration}


# The join key back to detail: every PACKET node carries the full 5-tuple as
# properties (SRC_IP/DST_IP/SRC_PORT/DST_PORT/PROTOCOL/SIZE/TIMESTAMP), so an IP is
# directly addressable with no traversal. anomaly_detection / fetch_traffic hand back
# an IP; these three queries turn that IP into its profile, its peer list, and a raw
# packet sample.

# One ENDPOINT (the aggregated profile) for a specific IP — the same fields
# fetch_traffic returns, scoped to $ip. Empty when the IP was captured but
# compute_embeddings hasn't run yet (the peers/packets below still resolve from raw
# PACKET nodes in that case).
_INSPECT_PROFILE_QUERY = """
MATCH (endpoint:ENDPOINT {IP_ADDRESS: $ip})
OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: $ip})<-[:OWNERSHIP]-(org:ORGANIZATION)
RETURN
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
    endpoint.INTERVAL_MEAN AS interval_mean,
    endpoint.INTERVAL_CV AS interval_cv,
    endpoint.OUTLIER AS outlier,
    endpoint.TIMESTAMP AS timestamp
"""

# True totals for the IP across the whole capture (NOT truncated by the peer/packet
# limits below), so the caller knows when the returned lists are samples.
_INSPECT_TOTALS_QUERY = """
MATCH (p:PACKET)
WHERE p.SRC_IP = $ip OR p.DST_IP = $ip
RETURN count(p) AS packets,
       count(DISTINCT CASE WHEN p.SRC_IP = $ip THEN p.DST_IP ELSE p.SRC_IP END) AS peers
"""

# The conversation breakdown: every other IP this one exchanged packets with, split by
# direction (outbound = this IP is the source). This is the handle the profile lacks —
# it stores OUT_PEERS as a count, never which peers. Ranked by total bytes so the
# heaviest conversations surface first.
_INSPECT_PEERS_QUERY = """
MATCH (p:PACKET)
WHERE p.SRC_IP = $ip OR p.DST_IP = $ip
WITH p,
     CASE WHEN p.SRC_IP = $ip THEN p.DST_IP ELSE p.SRC_IP END AS peer,
     (p.SRC_IP = $ip) AS outbound
WITH peer,
     sum(CASE WHEN outbound THEN p.SIZE ELSE 0 END) AS bytes_out,
     sum(CASE WHEN outbound THEN 1 ELSE 0 END) AS packets_out,
     sum(CASE WHEN NOT outbound THEN p.SIZE ELSE 0 END) AS bytes_in,
     sum(CASE WHEN NOT outbound THEN 1 ELSE 0 END) AS packets_in,
     collect(DISTINCT p.PROTOCOL) AS protocols,
     collect(DISTINCT p.SRC_PORT) AS src_ports,
     collect(DISTINCT p.DST_PORT) AS dst_ports
OPTIONAL MATCH (peer_ip:IP_ADDRESS {IP_ADDRESS: peer})<-[:OWNERSHIP]-(peer_org:ORGANIZATION)
RETURN peer AS peer_ip,
       peer_org.ORGANIZATION AS peer_org,
       peer_ip.HOSTNAME AS peer_hostname,
       peer_ip.LOCATION AS peer_location,
       bytes_out, packets_out, bytes_in, packets_in,
       (bytes_out + bytes_in) AS bytes_total,
       protocols, src_ports, dst_ports
ORDER BY bytes_total DESC
LIMIT $peer_limit
"""

# A raw, most-recent packet sample for the IP — for inspecting a specific conversation
# at 5-tuple granularity once the peer breakdown points somewhere interesting.
_INSPECT_PACKETS_QUERY = """
MATCH (p:PACKET)
WHERE p.SRC_IP = $ip OR p.DST_IP = $ip
RETURN p.SRC_IP AS src_ip, p.SRC_PORT AS src_port,
       p.DST_IP AS dst_ip, p.DST_PORT AS dst_port,
       p.PROTOCOL AS protocol, p.SIZE AS size,
       p.TIMESTAMP AS timestamp
ORDER BY p.TIMESTAMP DESC
LIMIT $packet_limit
"""


def _clean_ports(*port_lists) -> list[int]:
    """Merge collected SRC/DST port lists into one sorted set of real ports.

    DBSCAN's profile uses dst_port for both directions; for a human-facing peer
    breakdown the useful answer is just every non-ephemeral-placeholder port seen in
    the conversation, so we union src+dst, drop the 0/None placeholders, and sort.
    """
    ports = set()
    for lst in port_lists:
        for p in lst or []:
            if p:
                ports.add(int(p))
    return sorted(ports)


@mcp.tool(name="inspect_endpoint", description=(
    "Drill into ONE specific IP — the join key from an anomaly back to its detail. Hand it an IP (e.g. an "
    "outlier from anomaly_detection or any IP from fetch_traffic) and it returns, addressably by that IP: "
    "`profile` — the endpoint's aggregated profile (org/hostname/location, directional bytes/packets/peers/"
    "ports, timing, outlier flag), or null if the IP was captured but compute_embeddings hasn't run yet; "
    "`totals` — the true packet and distinct-peer counts for the IP across the whole capture (so you can tell "
    "when the lists below are samples); `peers` — WHO this IP actually exchanged packets with, one row per "
    "peer (peer IP + org/hostname/location, bytes/packets out & in, ports, protocols), ranked by total bytes; "
    "and `packets` — a most-recent raw 5-tuple packet sample. Directions are from the inspected IP's OWN "
    "perspective (outbound = this IP is the packet source): for a remote IP, its outbound bytes are what it "
    "sent TO the capture host (a host download), and its inbound bytes are what the host sent to it (outbound "
    "from host). `peer_limit` caps the peer rows, `packet_limit` caps the packet sample. This answers 'now "
    "show me this IP's packets and peers' without pulling and filtering the whole window client-side."
))
def inspect_endpoint(ip_address: str, peer_limit: int = 50, packet_limit: int = 20) -> dict[str, Any]:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            profile_rows = [r.data() for r in session.run(_INSPECT_PROFILE_QUERY, ip=ip_address)]
            totals = session.run(_INSPECT_TOTALS_QUERY, ip=ip_address).single()
            peer_rows = [r.data() for r in session.run(_INSPECT_PEERS_QUERY, ip=ip_address, peer_limit=peer_limit)]
            packet_rows = [r.data() for r in session.run(_INSPECT_PACKETS_QUERY, ip=ip_address, packet_limit=packet_limit)]
    except Exception as e:
        return {"ok": False, "error": f"could not inspect endpoint {ip_address!r} ({e})"}

    peers = []
    for r in peer_rows:
        peers.append({
            "peer_ip": r["peer_ip"],
            "peer_org": r["peer_org"],
            "peer_hostname": r["peer_hostname"],
            "peer_location": r["peer_location"],
            "bytes_out": r["bytes_out"],
            "packets_out": r["packets_out"],
            "bytes_in": r["bytes_in"],
            "packets_in": r["packets_in"],
            "bytes_total": r["bytes_total"],
            "protocols": sorted(p for p in (r["protocols"] or []) if p),
            "ports": _clean_ports(r["src_ports"], r["dst_ports"]),
        })

    total_packets = totals["packets"] if totals else 0
    total_peers = totals["peers"] if totals else 0

    payload = {
        "ip_address": ip_address,
        # True if the IP appears anywhere in the capture (raw packets) or as a profile.
        "found": bool(total_packets > 0 or profile_rows),
        "profile": profile_rows[0] if profile_rows else None,
        "totals": {"packets": total_packets, "peers": total_peers},
        "peers": peers,
        "peers_returned": len(peers),
        "packets": packet_rows,
        "packets_returned": len(packet_rows),
    }
    # Coerce Neo4j DateTime values (in profile.timestamp and each packet) to strings so
    # FastMCP can serialize the dict cleanly, matching fetch_traffic.
    payload = json.loads(json.dumps(payload, default=str))
    return {"ok": True, **payload}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdio", action="store_true", help="Serve over stdio (for MCP clients that spawn the server) instead of the default SSE HTTP server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")


if __name__ == "__main__":
    main()
