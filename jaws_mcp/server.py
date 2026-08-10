"""JAWS MCP Server — exposes the full JAWS network-analysis pipeline via MCPServer."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from jaws.config import (
    DATABASE,
    DEFAULT_PACKET_MODEL,
    PACKET_MODELS,
    SETTINGS,
    get_neo4j_driver,
    is_cloud_hosted,
)
from jaws.domain import legacy_failure, legacy_success

ROOT = Path(__file__).parent.parent  # /path/to/jaws/
SCRIPTS = ROOT / "jaws"

# No hard-coded wall-clock timeout. Capture is bounded by `duration`, and compute /
# anomaly detection are bounded by the dataset, so the scripts self-terminate — an
# arbitrary server-side number would only ever be wrong for someone's hardware. The
# real limit is the MCP client's own per-tool-call timeout (e.g. Claude Code's
# MCP_TOOL_TIMEOUT). An operator who wants a server-side backstop can set
# JAWS_MCP_TIMEOUT (seconds); by default there is none.
TIMEOUT = SETTINGS.runtime.mcp_timeout_seconds

INSTRUCTIONS = (
    """JAWS captures network traffic into a Neo4j graph, enriches it with OSINT, embeds it, and flags anomalies.

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
  - inspect_endpoint — drill into ONE IP (e.g. an outlier): its profile, its per-session history,
                       who it talked to (peers), and a raw packet sample. The join key from an
                       anomaly back to its detail.
  - list_captures   — enumerate the capture sessions accumulated in the graph.
  - drop_database   — wipe the graph entirely (optional between sessions — see Notes).

Notes:
  - Keep captures short (30-120s); capture again rather than running one long session.
  - Captures ACCUMULATE as sessions: each capture_packets run is stamped with a capture_id, and
    compute_embeddings profiles only the LATEST session by default (pass session='all' or a
    specific capture_id to change that). You do NOT need drop_database between captures; packet
    history stays queryable via list_captures/inspect_endpoint while profiles track one session.
  - REPEATED RUNS MAKE DETECTION BETTER. Endpoint profiles accumulate one set per session, so
    anomaly_detection scores an endpoint against its OWN history where it has one instead of only
    against its current peers — which suppresses the endpoints that are always busy and surfaces
    the ones that CHANGED, plus endpoints never seen before (`first_seen`). This needs no extra
    steps: just run capture → document → compute → detect again, and check `baseline.enabled` in
    the result. Dropping the database throws that history away, so prefer not to.
  - After every capture, run document_organizations and compute_embeddings before anomaly_detection.
  - Use compute_embeddings(api='transformers') on a GPU host; otherwise api='openai'. The local
    transformer model must be downloaded on the host beforehand (`jaws-utils --model ...`); this is
    a one-time setup step done outside the MCP.
  - compute_embeddings and anomaly_detection can run for a while on large captures — if your client
    aborts them early, raise its per-tool-call timeout (e.g. Claude Code's MCP_TOOL_TIMEOUT).
  - All tools operate on the single '%s' database."""
    % DATABASE
)

mcp = MCPServer("JAWS - Wireshark MCP with Network Analysis Tools", instructions=INSTRUCTIONS)


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
    makes MCPServer emit it as structuredContent verbatim, instead of wrapping a JSON
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
        return legacy_failure(f"process exceeded the JAWS_MCP_TIMEOUT backstop of {timeout}s")
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
        return legacy_failure(err or out or "no output", exit_code=result.returncode)
    if isinstance(parsed, dict):
        # Already enveloped by the Reporter; backstop ok in case a CLI printed a
        # bare dict outside reporter.result.
        parsed.setdefault("ok", True)
        return parsed
    if parsed is not None:
        # Valid JSON that isn't an object (e.g. a bare array) — keep it addressable.
        return legacy_success({"result": parsed})
    if not out:
        return legacy_failure("process produced no output")
    # Unexpected non-JSON on stdout in agent mode — a contract violation. Preserve
    # it rather than crash, but mark it failed so the client doesn't read it as a result.
    return legacy_failure("process produced non-JSON output", raw=out)


def _script(name: str, *args: str) -> dict[str, Any]:
    return _run([sys.executable, str(SCRIPTS / name), *args])


@mcp.tool(
    name="list_interfaces",
    description=(
        "Step 1. List the physical network interfaces available for capture, one per line. "
        "Virtual/loopback interfaces (lo, docker, tailscale) are already filtered out. "
        "Pick one of these names to pass to capture_packets."
    ),
)
def list_interfaces() -> dict[str, Any]:
    return _script("jaws_capture.py", "--list")


@mcp.tool(
    name="capture_packets",
    description=(
        "Step 2. Capture live packets from an interface into the graph for `duration` seconds. "
        "Use an interface name from list_interfaces. Keep captures short (30-120s) and capture "
        "again rather than running one long session. The call runs for roughly `duration` seconds. "
        "Each run becomes its own capture SESSION (the result's `capture_id`); sessions accumulate "
        "in the graph, and compute_embeddings profiles the latest one by default — no need to "
        "drop_database between captures."
    ),
)
def capture_packets(interface: str, duration: int = 60) -> dict[str, Any]:
    return _script(
        "jaws_capture.py",
        "--interface",
        interface,
        "--duration",
        str(duration),
    )


_CAPTURES_QUERY = """
MATCH (c:CAPTURE)
RETURN c.CAPTURE_ID AS capture_id, c.SOURCE AS source,
       c.STARTED AS started, c.PACKETS AS packets
ORDER BY c.STARTED DESC
"""

# Every profile set stored in the graph, newest-computed first. Profile sets accumulate
# (one per compute run), so this is the per-endpoint history the anomaly baseline draws
# on; the first row is the set the read tools and anomaly_detection default to.
_PROFILED_QUERY = """
MATCH (e:ENDPOINT)
RETURN e.CAPTURE_ID AS session, count(e) AS endpoints, max(e.TIMESTAMP) AS computed
ORDER BY computed DESC
"""


@mcp.tool(
    name="list_captures",
    description=(
        "List the capture sessions accumulated in the graph, newest first: each with its `capture_id`, "
        "`source` (interface or imported pcap path), `started` timestamp, and `packets` count. "
        "`profiled_sessions` lists the endpoint-profile sets stored in the graph (newest computed first, "
        "each with its endpoint count) — profiles accumulate one set per compute run, and that accumulated "
        "history is what anomaly_detection baselines each endpoint against. `profiled_session` names the one "
        "the read tools and anomaly_detection default to ('all', a capture_id, or null when "
        "compute_embeddings hasn't run); `baseline_sessions` counts the prior sets available to baseline "
        "against, so 0 means scores are still purely peer-relative and history is only now accumulating. "
        "Use a capture_id with compute_embeddings(session=...) to re-profile an older session, or with "
        "anomaly_detection(session=...) to re-analyze a stored profile set."
    ),
)
def list_captures() -> dict[str, Any]:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            captures = [record.data() for record in session.run(_CAPTURES_QUERY)]
            profiled = [record.data() for record in session.run(_PROFILED_QUERY)]
    except Exception as e:
        return legacy_failure(f"could not list captures ({e})")
    captures = json.loads(json.dumps(captures, default=str))
    profiled = json.loads(json.dumps(profiled, default=str))
    current = profiled[0]["session"] if profiled else None
    return legacy_success(
        {
            "captures": captures,
            "count": len(captures),
            "profiled_session": current,
            "profiled_sessions": profiled,
            "baseline_sessions": len(
                [p for p in profiled if p["session"] not in (current, "all", None)]
            ),
        }
    )


@mcp.tool(
    name="document_organizations",
    description=(
        "Step 3. Enrich the captured IP addresses with organization/ASN ownership via Ipinfo. "
        "Run after each capture and before compute_embeddings."
    ),
)
def document_organizations() -> dict[str, Any]:
    return _script("jaws_ipinfo.py")


@mcp.tool(
    name="compute_embeddings",
    description=(
        "Step 4. Aggregate each IP's captured traffic (both directions) into an endpoint profile and embed "
        "that profile — one vector per IP — for downstream clustering. "
        "Use api='transformers' (the default HERE; the CLI's default is 'openai') on a GPU host — the local "
        "model produces tighter clusters and surfaces anomalies that OpenAI embeddings miss (the model must "
        "be pre-downloaded on the host). Use api='openai' as a fallback when no GPU is available. "
        f"`model` selects the local transformers model when api='transformers': one of {list(PACKET_MODELS)} "
        f"(default '{DEFAULT_PACKET_MODEL}'); ignored for api='openai'. "
        "`session` selects which capture session to profile: 'latest' (default), 'all' (every packet in "
        "the graph — inter-packet timing still never crosses session boundaries), or a capture_id from "
        "list_captures. Each run rebuilds the profile set for THAT scope only and leaves other sessions' "
        "profile sets in place, so profiles accumulate one set per session: that is the per-endpoint history "
        "anomaly_detection baselines against, and it is why repeated capture→compute→detect cycles get more "
        "discriminating over time. The result's `profiled_sessions` counts the sets now stored (1 = no history "
        "yet). `retain_profiles` caps how many sets are kept (default 20, 0 = unlimited); raw packet history is "
        "never pruned. May run for a while on large captures."
    ),
)
def compute_embeddings(
    api: str = "transformers",
    model: str = DEFAULT_PACKET_MODEL,
    session: str = "latest",
    retain_profiles: int = 20,
) -> dict[str, Any]:
    if model not in PACKET_MODELS:
        return legacy_failure(f"unknown model '{model}'; available: {list(PACKET_MODELS)}")
    return _script(
        "jaws_compute.py",
        "--api",
        api,
        "--model",
        model,
        "--session",
        session,
        "--retain-profiles",
        str(retain_profiles),
    )


@mcp.tool(
    name="anomaly_detection",
    description=(
        "Step 5. Cluster the per-IP endpoint embeddings with PCA + DBSCAN and score every IP for anomaly. "
        "Returns a JSON summary: endpoints_clustered, outliers_flagged, `clusters`/`cluster_sizes` (so 0 "
        "outliers from one tight cluster reads differently than 0 from an over-generous eps; `cluster_sizes` "
        "excludes DBSCAN noise, so sum(cluster_sizes) + outliers_flagged == endpoints_clustered), the DBSCAN "
        "params (eps/min_samples/components), a `units` map labeling the raw numbers, and `endpoints` — the FULL "
        "list of clustered IPs sorted by `anomaly_score` (descending), so there is always a ranking to "
        "triage even when DBSCAN flags nothing. Each endpoint carries `anomaly_score` (behavioral distance "
        "from the typical host: L2 norm of its top-3 |robust-z| components, 'low' deviations down-weighted "
        "and capped — see the result's `scoring` field), `is_outlier` (the DBSCAN "
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
        "Multicast/broadcast addresses (SSDP/mDNS chatter — one-way by construction) are excluded from both "
        "rankings and listed under `excluded_non_conversational`; each ranked endpoint carries its "
        "`endpoint_type` (public/private/multicast/…) and `cloud_hosted` — true when the org's ASN is a "
        "hosting/CDN provider (GCP/AWS/Cloudflare/…), meaning the org label names the infrastructure "
        "provider, NOT the actual service behind the IP — don't clear a finding on the provider's name. "
        "`components` is the number of PCA dimensions to retain (minimum 2); if the result's "
        "`pca_variance_total` comes back well under ~0.5, the projection is dropping structure — re-run "
        "with components=3. `whiten` scales each PCA "
        "component to unit variance — helps with a few strong components but amplifies noise when many are retained. "
        "`eps` overrides the DBSCAN epsilon; when omitted it is auto-recommended, but that recommendation "
        "tends to overshoot on small captures and return 0 outliers — if outliers_flagged is 0 and you "
        "expected some, re-run with a smaller eps (e.g. 50-70% of the eps shown in the result). "
        "`feature_weight` controls how much each endpoint's behavioral numbers (bytes/packets/peers, in & "
        "out) drive clustering vs. the text profile: 0 clusters on text/org/protocol only, higher (default "
        "1.0) surfaces volume/fan-out anomalies like unusual outbound traffic. "
        "The capture host itself is excluded by default (it is a structural hub that dominates clustering; "
        "its outbound traffic still appears as remote endpoints' inbound) — set include_local=true to keep it. "
        "HISTORICAL BASELINE: when the same IPs have been profiled in earlier capture sessions, an endpoint is "
        "scored against ITS OWN past rather than only against its current peers — so a server that is always "
        "the heaviest talker stops topping the ranking for volume it posts every single run, and a change in "
        "its behavior ranks instead. Every reason states which reference produced it via `compared_to` ('own "
        "history' or 'peer endpoints') and, for historical ones, the `baseline` value and how many sessions "
        "back it. Cadence features (interval_mean/interval_cv) are NEVER baselined — a beacon looks identical "
        "in every session, so its own history would declare it normal — and stay peer-relative; the result's "
        "`baseline.exempt_features` names them. Each endpoint also carries `baseline_sessions` (prior sessions "
        "behind its baseline) and `first_seen` (true = never observed in any earlier session, a finding on its "
        "own that no per-feature score can express; null when there is no history at all), with the full list "
        "under `baseline.first_seen`. Read `baseline.enabled`: false means only one profile set exists and "
        "every score is peer-relative — run more capture→compute cycles and detection sharpens. Set "
        "baseline=false to force purely peer-relative scoring. `session` analyzes a specific stored profile "
        "set (a capture_id from list_captures) instead of the most recent."
    ),
)
def anomaly_detection(
    components: int = 2,
    whiten: bool = False,
    eps: float | None = None,
    feature_weight: float = 1.0,
    include_local: bool = False,
    session: str = "latest",
    baseline: bool = True,
) -> dict[str, Any]:
    args = [
        "--components",
        str(components),
        "--feature-weight",
        str(feature_weight),
        "--session",
        session,
    ]
    if whiten:
        args.append("--whiten")
    if eps is not None:
        args += ["--eps", str(eps)]
    if include_local:
        args.append("--include-local")
    if not baseline:
        args.append("--no-baseline")
    return _script("jaws_finder.py", *args)


@mcp.tool(
    name="drop_database",
    description=(
        "Wipe ALL data from the graph. Irreversible. Typically run before starting a fresh capture session."
    ),
)
def drop_database() -> dict[str, Any]:
    return _script("jaws_utils.py")


# Endpoint profiles ACCUMULATE, one set per capture session, so every profile read must
# pin a session first — an unscoped MATCH returns the same IP once per session it was
# ever seen in. This resolves the most recently COMPUTED set, which is what the pipeline
# just produced and what anomaly_detection analyzes by default. The null branch covers a
# legacy graph whose profiles predate session stamping.
_LATEST_SCOPE = """
CALL () {
    MATCH (e:ENDPOINT)
    RETURN e.CAPTURE_ID AS scope
    ORDER BY e.TIMESTAMP DESC
    LIMIT 1
}
"""

# Ranked by total bytes, not recency: every profile in a compute run shares one
# TIMESTAMP, so a recency sort is degenerate and `limit` would truncate arbitrarily.
_FETCH_QUERY = (
    _LATEST_SCOPE
    + """
MATCH (endpoint:ENDPOINT)
WHERE (endpoint.CAPTURE_ID = scope OR (scope IS NULL AND endpoint.CAPTURE_ID IS NULL))
  AND endpoint.TIMESTAMP > datetime() - duration({minutes: $duration})
OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: endpoint.IP_ADDRESS})<-[:OWNERSHIP]-(org:ORGANIZATION)
RETURN
    endpoint.IP_ADDRESS AS ip_address,
    endpoint.ENDPOINT_TYPE AS endpoint_type,
    endpoint.CAPTURE_ID AS capture_id,
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
ORDER BY endpoint.BYTES_OUT + endpoint.BYTES_IN DESC
LIMIT $limit
"""
)


@mcp.tool(
    name="fetch_traffic",
    description=(
        "Read processed per-IP endpoint profiles back from the graph. Returns an object with an `endpoints` "
        "list (ranked by total bytes, heaviest conversations first) and a `count`; each endpoint is one IP "
        "with its org/hostname/location and "
        "directional traffic (bytes/packets/peers/ports, outbound and inbound), plus its outlier flag, "
        "`endpoint_type` (public/private/multicast/… — multicast/broadcast rows are protocol chatter, not "
        "conversation partners, and are excluded from anomaly rankings), and `cloud_hosted` (the org's ASN "
        "is a hosting/CDN provider, so the org names the infrastructure provider, not the actual service). "
        "Directions are from the endpoint's OWN perspective: for a remote IP, `bytes_out` is what it sent TO "
        "the capture host (a host download), and `bytes_in` is what the host sent to it (outbound from host). "
        "Profiles accumulate one set per capture session; this returns the MOST RECENTLY COMPUTED set (one row "
        "per IP), so it is a snapshot, not a timeseries — use inspect_endpoint's `history` for one IP across "
        "sessions, or list_captures for what else is stored. "
        "`duration_minutes` is how many minutes back to include, measured by when each profile was COMPUTED "
        "(the compute_embeddings run), not when the traffic occurred; `limit` caps the rows. "
        "This is the windowed overview; to drill into ONE specific IP (e.g. an outlier from anomaly_detection) "
        "and see exactly who it talked to, use inspect_endpoint instead."
    ),
)
def fetch_traffic(duration_minutes: int = 60, limit: int = 100) -> dict[str, Any]:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            result = session.run(_FETCH_QUERY, duration=duration_minutes, limit=limit)
            data = [record.data() for record in result]
    except Exception as e:
        return legacy_failure(f"could not fetch endpoints ({e})")
    # Round-trip through json with default=str to coerce Neo4j DateTime values into
    # JSON-native strings, so MCPServer can serialize the returned dict cleanly.
    endpoints = json.loads(json.dumps(data, default=str))
    for endpoint in endpoints:
        endpoint["cloud_hosted"] = is_cloud_hosted(endpoint.get("org"))
    return legacy_success(
        {
            "endpoints": endpoints,
            "count": len(endpoints),
            "duration_minutes": duration_minutes,
        }
    )


# The join key back to detail: every PACKET node carries the full 5-tuple as
# properties (SRC_IP/DST_IP/SRC_PORT/DST_PORT/PROTOCOL/SIZE/TIMESTAMP), so an IP is
# directly addressable with no traversal. anomaly_detection / fetch_traffic hand back
# an IP; these three queries turn that IP into its profile, its peer list, and a raw
# packet sample.

# One ENDPOINT (the aggregated profile) for a specific IP — the same fields
# fetch_traffic returns, scoped to $ip. Empty when the IP was captured but
# compute_embeddings hasn't run yet (the peers/packets below still resolve from raw
# PACKET nodes in that case). Profiles accumulate per session, so this takes the IP's
# most recently computed one; _INSPECT_HISTORY_QUERY returns the rest as a series.
_INSPECT_PROFILE_QUERY = """
MATCH (endpoint:ENDPOINT {IP_ADDRESS: $ip})
WITH endpoint ORDER BY endpoint.TIMESTAMP DESC LIMIT 1
OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: $ip})<-[:OWNERSHIP]-(org:ORGANIZATION)
RETURN
    endpoint.IP_ADDRESS AS ip_address,
    endpoint.ENDPOINT_TYPE AS endpoint_type,
    endpoint.CAPTURE_ID AS capture_id,
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

# The same IP's profile in every session it was seen in, newest first — the timeseries
# view that a single profile cannot give. This is what turns "is 2 GB a lot?" into "it
# moved 40 MB in each of the last five sessions and 2 GB in this one", and what the
# anomaly baseline is computed from, so an agent can audit a baselined finding rather
# than take the score on faith. The pooled 'all' scope is excluded: it re-aggregates
# every session at once, so it is not a point in the series.
_INSPECT_HISTORY_QUERY = """
MATCH (endpoint:ENDPOINT {IP_ADDRESS: $ip})
WHERE endpoint.CAPTURE_ID IS NOT NULL AND endpoint.CAPTURE_ID <> 'all'
RETURN endpoint.CAPTURE_ID AS capture_id,
       endpoint.BYTES_OUT AS bytes_out,
       endpoint.PACKETS_OUT AS packets_out,
       endpoint.OUT_PEERS AS out_peers,
       endpoint.BYTES_IN AS bytes_in,
       endpoint.PACKETS_IN AS packets_in,
       endpoint.IN_PEERS AS in_peers,
       endpoint.INTERVAL_MEAN AS interval_mean,
       endpoint.INTERVAL_CV AS interval_cv,
       endpoint.OUTLIER AS outlier
ORDER BY capture_id DESC
LIMIT $history_limit
"""

# True totals for the IP across EVERY session in the graph (NOT truncated by the
# peer/packet limits below), so the caller knows when the returned lists are samples.
# The profile above is scoped to one session, so totals can legitimately exceed it —
# the payload labels this with totals.scope so the mismatch doesn't read as a bug.
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
# Ports are split by role via the flow heuristic: per packet, the service-identifying
# side is min(src, dst) — ephemeral client ports are allocated high (Linux default
# 32768+), so a naive src∪dst union balloons with one throwaway port per connection
# across sessions while burying the one port that says what the conversation IS. The
# high side is collected only to be counted (churn signal); exact per-packet ports
# remain available in the `packets` sample. Port-0 placeholders (no TCP/UDP layer)
# yield null from the guarded CASE and collect() skips nulls.
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
     collect(DISTINCT CASE WHEN p.SRC_PORT > 0 AND p.DST_PORT > 0
                           THEN CASE WHEN p.SRC_PORT < p.DST_PORT THEN p.SRC_PORT ELSE p.DST_PORT END
                      END) AS service_ports,
     collect(DISTINCT CASE WHEN p.SRC_PORT > 0 AND p.DST_PORT > 0
                           THEN CASE WHEN p.SRC_PORT < p.DST_PORT THEN p.DST_PORT ELSE p.SRC_PORT END
                      END) AS high_ports
OPTIONAL MATCH (peer_ip:IP_ADDRESS {IP_ADDRESS: peer})<-[:OWNERSHIP]-(peer_org:ORGANIZATION)
RETURN peer AS peer_ip,
       peer_org.ORGANIZATION AS peer_org,
       peer_ip.HOSTNAME AS peer_hostname,
       peer_ip.LOCATION AS peer_location,
       bytes_out, packets_out, bytes_in, packets_in,
       (bytes_out + bytes_in) AS bytes_total,
       protocols, service_ports, high_ports
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


def _split_ports(service_ports, high_ports) -> tuple[list[int], int]:
    """Turn the query's per-role port collections into (service_ports, ephemeral count).

    A port equal on both sides of a packet (e.g. NTP 123↔123) lands in both
    collections; subtracting the service set keeps it from double-counting as
    ephemeral churn.
    """
    service = sorted(int(p) for p in (service_ports or []) if p)
    ephemeral = {int(p) for p in (high_ports or []) if p} - set(service)
    return service, len(ephemeral)


@mcp.tool(
    name="inspect_endpoint",
    description=(
        "Drill into ONE specific IP — the join key from an anomaly back to its detail. Hand it an IP (e.g. an "
        "outlier from anomaly_detection or any IP from fetch_traffic) and it returns, addressably by that IP: "
        "`profile` — the endpoint's aggregated profile (org/hostname/location, a `cloud_hosted` hosting-ASN "
        "hint, directional bytes/packets/peers/"
        "ports, timing, outlier flag), or null if the IP was captured but compute_embeddings hasn't run yet — "
        "the profile describes ONE capture session (its `capture_id`); "
        "`totals` — the true packet and distinct-peer counts for the IP across every session in the graph "
        "(labeled `scope: all_sessions` — totals exceeding the profile's counts means older sessions also saw "
        "this IP, not an inconsistency; it also tells you when the lists below are samples); `peers` — WHO this "
        "IP actually exchanged packets with across all sessions, one row per "
        "peer (peer IP + org/hostname/location, bytes/packets out & in, protocols, `service_ports` — the "
        "service-identifying low side of each port pair, e.g. 443 — and `ephemeral_ports`, a count of distinct "
        "high-side client ports: high churn means many short-lived connections rather than one long tunnel), "
        "ranked by total bytes; "
        "`history` — this IP's profile in EVERY capture session it appeared in, newest first (bytes/packets/"
        "peers per direction, timing, and that session's outlier verdict), with `sessions_seen` counting them: "
        "the timeseries behind the profile, and the series anomaly_detection baselines an endpoint against, so "
        "a 'far above its own history' finding can be audited against the real numbers (`history_limit` caps "
        "the rows); "
        "and `packets` — a most-recent raw 5-tuple packet sample. Directions are from the inspected IP's OWN "
        "perspective (outbound = this IP is the packet source): for a remote IP, its outbound bytes are what it "
        "sent TO the capture host (a host download), and its inbound bytes are what the host sent to it (outbound "
        "from host). `peer_limit` caps the peer rows, `packet_limit` caps the packet sample. This answers 'now "
        "show me this IP's packets and peers' without pulling and filtering the whole window client-side."
    ),
)
def inspect_endpoint(
    ip_address: str, peer_limit: int = 50, packet_limit: int = 20, history_limit: int = 20
) -> dict[str, Any]:
    try:
        driver = get_neo4j_driver()
        with driver.session(database=DATABASE) as session:
            profile_rows = [r.data() for r in session.run(_INSPECT_PROFILE_QUERY, ip=ip_address)]
            totals = session.run(_INSPECT_TOTALS_QUERY, ip=ip_address).single()
            peer_rows = [
                r.data()
                for r in session.run(_INSPECT_PEERS_QUERY, ip=ip_address, peer_limit=peer_limit)
            ]
            packet_rows = [
                r.data()
                for r in session.run(
                    _INSPECT_PACKETS_QUERY, ip=ip_address, packet_limit=packet_limit
                )
            ]
            history_rows = [
                r.data()
                for r in session.run(
                    _INSPECT_HISTORY_QUERY, ip=ip_address, history_limit=history_limit
                )
            ]
    except Exception as e:
        return legacy_failure(f"could not inspect endpoint {ip_address!r} ({e})")

    peers = []
    for r in peer_rows:
        service_ports, ephemeral_ports = _split_ports(r["service_ports"], r["high_ports"])
        peers.append(
            {
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
                "service_ports": service_ports,
                "ephemeral_ports": ephemeral_ports,
            }
        )

    total_packets = totals["packets"] if totals else 0
    total_peers = totals["peers"] if totals else 0

    profile = profile_rows[0] if profile_rows else None
    if profile:
        profile["cloud_hosted"] = is_cloud_hosted(profile.get("org"))

    payload = {
        "ip_address": ip_address,
        # True if the IP appears anywhere in the capture (raw packets) or as a profile.
        "found": bool(total_packets > 0 or profile_rows),
        "profile": profile,
        "totals": {"packets": total_packets, "peers": total_peers, "scope": "all_sessions"},
        # This IP's profile in each session it was seen in, newest first — the series the
        # anomaly baseline is derived from, so a "far above its own history" finding can
        # be checked against the actual numbers.
        "history": history_rows,
        "sessions_seen": len(history_rows),
        "peers": peers,
        "peers_returned": len(peers),
        "packets": packet_rows,
        "packets_returned": len(packet_rows),
    }
    # Coerce Neo4j DateTime values (in profile.timestamp and each packet) to strings so
    # MCPServer can serialize the dict cleanly, matching fetch_traffic.
    payload = json.loads(json.dumps(payload, default=str))
    return legacy_success(payload)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Serve over stdio (for MCP clients that spawn the server) instead of the default SSE HTTP server.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
