import argparse
from rich.console import Group
import numpy as np
import pandas as pd
import torch
from sentence_transformers import SentenceTransformer
from jaws.config import (
    CONSOLE,
    DATABASE,
    PACKET_MODELS,
    DEFAULT_PACKET_MODEL,
    OPENAI_EMBEDDING_MODEL,
    get_openai_client,
)
from jaws.jaws_utils import (
    dbms_connection,
    Reporter,
    render_info_panel,
    render_activity_panel,
    classify_endpoint,
    MIN_TIMING_PACKETS
)


def fetch_packets(driver, database, capture_id=None):
    # PACKET nodes carry the 5-tuple + size as properties, so per-IP aggregation
    # reads straight off them (one scan) — no traversal needed. `capture_id` scopes
    # the scan to one session; None means every packet in the graph (--session all,
    # or a legacy graph with no CAPTURE nodes).
    query = """
    MATCH (p:PACKET)
    WHERE $capture_id IS NULL OR p.CAPTURE_ID = $capture_id
    RETURN p.SRC_IP AS src_ip, p.DST_IP AS dst_ip,
           p.SRC_PORT AS src_port, p.DST_PORT AS dst_port,
           p.SIZE AS size, p.PROTOCOL AS protocol,
           p.TIMESTAMP.epochMillis AS ts_ms,
           p.CAPTURE_ID AS capture_id
    """
    with driver.session(database=database) as session:
        result = session.run(query, capture_id=capture_id)
        df = pd.DataFrame([record.data() for record in result])
    return df


def resolve_session(driver, database, session_arg):
    """Turn --session (latest | all | <capture id>) into a concrete packet scope.

    Returns (capture_id, session_ids): `capture_id` is the concrete session to filter
    packets on, or None for no filter ('all', or a legacy graph with no CAPTURE
    nodes); `session_ids` is every session in the graph, oldest first, so callers can
    report what was available. Raises ValueError for an explicit id that doesn't exist.
    """
    query = "MATCH (c:CAPTURE) RETURN c.CAPTURE_ID AS id ORDER BY c.STARTED"
    with driver.session(database=database) as session:
        session_ids = [record["id"] for record in session.run(query)]
    if session_arg == "all":
        return None, session_ids
    if session_arg == "latest":
        # A legacy graph (captured before sessions existed) has packets but no
        # CAPTURE nodes — treat the whole graph as one implicit session.
        return (session_ids[-1] if session_ids else None), session_ids
    if session_arg not in session_ids:
        raise ValueError(
            f"session '{session_arg}' not found; available: {session_ids or 'none (no CAPTURE nodes — use latest or all)'}")
    return session_arg, session_ids


def fetch_ip_metadata(driver, database):
    # Org/hostname/location per IP, set by jaws_ipinfo (org name on the org node,
    # hostname/location on the IP node).
    query = """
    MATCH (ip:IP_ADDRESS)
    OPTIONAL MATCH (ip)<-[:OWNERSHIP]-(org:ORGANIZATION)
    RETURN ip.IP_ADDRESS AS ip_address,
           org.ORGANIZATION AS org,
           ip.HOSTNAME AS hostname,
           ip.LOCATION AS location
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        return {record["ip_address"]: record.data() for record in result}


def endpoint_timing(ts_groups):
    """Inter-packet cadence for one endpoint's combined packet stream.

    `ts_groups` is a list of timestamp arrays (ms), ONE PER CAPTURE SESSION: intervals
    are computed within each group and pooled, never across groups, so the dead gap
    between two capture runs doesn't register as one giant interval (which would
    inflate a real beacon's CV — the opposite failure of the tiny-burst false beacon).
    Returns (interval_mean, interval_cv) in seconds, or (None, None) when the pooled
    intervals are fewer than MIN_TIMING_PACKETS - 1 (the single-stream equivalent of
    the packet gate). `interval_cv` (std/mean of inter-packet gaps) is the beaconing
    signal: a low CV means highly regular callbacks (C2-like), a high CV means
    bursty/human traffic. `interval_mean` is the typical gap, i.e. the period.
    """
    diffs = []
    for ts_ms in ts_groups:
        ts = np.sort(np.asarray(ts_ms, dtype=float)) / 1000.0
        ts = ts[~np.isnan(ts)]
        if len(ts) >= 2:
            diffs.append(np.diff(ts))
    if not diffs:
        return None, None
    diffs = np.concatenate(diffs)
    if len(diffs) < MIN_TIMING_PACKETS - 1:
        return None, None
    mean = float(diffs.mean())
    if mean <= 0:
        return None, None
    cv = float(diffs.std() / mean)
    return mean, cv


def build_endpoint_profiles(packets, metadata):
    """Aggregate every packet into one profile per IP address, split by direction.

    Each IP becomes a single data point describing its outbound traffic (as the
    source) and inbound traffic (as the destination), so outbound anomalies are
    first-class. The local host is included intentionally.
    """
    if packets.empty:
        return []
    # '0.0.0.0' is the placeholder for packets with no IP layer — not a real endpoint.
    packets = packets[(packets["src_ip"] != "0.0.0.0") & (packets["dst_ip"] != "0.0.0.0")]
    if packets.empty:
        return []

    def aggregate(ip_col, peer_col, port_col):
        result = {}
        for ip, g in packets.groupby(ip_col):
            result[ip] = {
                "bytes": int(g["size"].sum()),
                "packets": int(len(g)),
                "peers": int(g[peer_col].nunique()),
                "ports": sorted({int(p) for p in g[port_col].dropna()})[:20],
                "protocols": sorted({str(p) for p in g["protocol"].dropna()}),
            }
        return result

    # Outbound: IP is the source — peers are destinations, ports are services it contacted.
    outbound = aggregate("src_ip", "dst_ip", "dst_port")
    # Inbound: IP is the destination — peers are sources, ports are its own that received.
    inbound = aggregate("dst_ip", "src_ip", "dst_port")

    # Timing is computed over each IP's combined stream (every packet it sends OR
    # receives), so a single-peer endpoint with regular callbacks reads as low-CV
    # while a busy multi-peer server's interleaved conversations read as high-CV.
    # The stream is split per capture session (legacy packets with no CAPTURE_ID
    # group together) so intervals never span the gap between two capture runs.
    has_ts = "ts_ms" in packets.columns
    timing = {}
    if has_ts:
        session_col = packets["capture_id"].fillna("") if "capture_id" in packets.columns \
            else pd.Series("", index=packets.index)
        for ip in set(packets["src_ip"]) | set(packets["dst_ip"]):
            mask = (packets["src_ip"] == ip) | (packets["dst_ip"] == ip)
            stream = packets.loc[mask, "ts_ms"]
            groups = [g.values for _, g in stream.groupby(session_col.loc[mask])]
            timing[ip] = endpoint_timing(groups)

    profiles = []
    for ip in sorted(set(outbound) | set(inbound)):
        out = outbound.get(ip, {})
        inb = inbound.get(ip, {})
        meta = metadata.get(ip, {})
        interval_mean, interval_cv = timing.get(ip, (None, None))
        profiles.append({
            "ip_address": ip,
            # Address scope (public/private/multicast/…) via stdlib ipaddress. Stored on
            # the node so readers can tell a real conversation partner from protocol
            # chatter; the finder keeps non-conversational types out of the rankings.
            "endpoint_type": classify_endpoint(ip),
            "org": meta.get("org"),
            "hostname": meta.get("hostname"),
            "location": meta.get("location"),
            "bytes_out": out.get("bytes", 0),
            "packets_out": out.get("packets", 0),
            "out_peers": out.get("peers", 0),
            "out_ports": out.get("ports", []),
            "bytes_in": inb.get("bytes", 0),
            "packets_in": inb.get("packets", 0),
            "in_peers": inb.get("peers", 0),
            "in_ports": inb.get("ports", []),
            "protocols": sorted(set(out.get("protocols", [])) | set(inb.get("protocols", []))),
            "interval_mean": interval_mean,
            "interval_cv": interval_cv,
        })
    return profiles


def build_endpoint_description(p):
    return (
        f"IP: {p['ip_address']} ({p['endpoint_type']}) | Organization: {p['org']} | Hostname: {p['hostname']} | Location: {p['location']}\n"
        f"Outbound: {p['bytes_out']} bytes, {p['packets_out']} packets to {p['out_peers']} peers | Ports: {p['out_ports']}\n"
        f"Inbound: {p['bytes_in']} bytes, {p['packets_in']} packets from {p['in_peers']} peers | Ports: {p['in_ports']}\n"
        f"Protocols: {p['protocols']}\n"
    )


# The ENDPOINT layer is the CURRENT ANALYSIS, not history: profiles describe one
# session scope and are cheap derived data, while PACKET/CAPTURE nodes are the
# durable record. Clearing before each compute keeps the finder from ranking stale
# profiles left over from a previous session (an IP seen in session A but not B
# would otherwise survive a session-B compute with session-A numbers).
def clear_endpoints(driver, database):
    with driver.session(database=database) as session:
        result = session.run("MATCH (e:ENDPOINT) DETACH DELETE e RETURN count(e) AS cleared")
        return result.single()["cleared"]


def add_endpoint_to_database(profile, embedding, session_scope, driver, database):
    query = """
    MATCH (ip:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (ip)-[:PROFILE]->(endpoint:ENDPOINT {IP_ADDRESS: $ip_address})
    SET endpoint.EMBEDDING = $embedding,
        endpoint.CAPTURE_ID = $session_scope,
        endpoint.ENDPOINT_TYPE = $endpoint_type,
        endpoint.ORGANIZATION = $org,
        endpoint.HOSTNAME = $hostname,
        endpoint.LOCATION = $location,
        endpoint.BYTES_OUT = $bytes_out,
        endpoint.PACKETS_OUT = $packets_out,
        endpoint.OUT_PEERS = $out_peers,
        endpoint.OUT_PORTS = $out_ports,
        endpoint.BYTES_IN = $bytes_in,
        endpoint.PACKETS_IN = $packets_in,
        endpoint.IN_PEERS = $in_peers,
        endpoint.IN_PORTS = $in_ports,
        endpoint.PROTOCOLS = $protocols,
        endpoint.INTERVAL_MEAN = $interval_mean,
        endpoint.INTERVAL_CV = $interval_cv,
        endpoint.TIMESTAMP = datetime()
    """
    with driver.session(database=database) as session:
        session.run(query,
                    ip_address=profile["ip_address"], embedding=embedding,
                    session_scope=session_scope,
                    endpoint_type=profile["endpoint_type"],
                    org=profile["org"], hostname=profile["hostname"], location=profile["location"],
                    bytes_out=profile["bytes_out"], packets_out=profile["packets_out"],
                    out_peers=profile["out_peers"], out_ports=profile["out_ports"],
                    bytes_in=profile["bytes_in"], packets_in=profile["packets_in"],
                    in_peers=profile["in_peers"], in_ports=profile["in_ports"],
                    protocols=profile["protocols"],
                    interval_mean=profile.get("interval_mean"),
                    interval_cv=profile.get("interval_cv"))


device = "cuda" if torch.cuda.is_available() else "cpu"
def compute_transformer_embeddings(descriptions, embedder):
    # sentence-transformers reads each model's own pooling config and applies it; with
    # normalize_embeddings it L2-normalizes for calibrated cosine geometry downstream.
    # One function works for any model in PACKET_MODELS — no per-model code. Encoding
    # the whole profile set in one call lets the library batch on the device, which is
    # dramatically faster than per-profile encodes on a GPU.
    return embedder.encode(descriptions, normalize_embeddings=True).tolist()


# One request per chunk instead of one per profile — fewer round-trips and less
# rate-limit exposure. 512 short profiles stays well inside the API's per-request
# input-count and token limits.
OPENAI_EMBEDDING_BATCH = 512

def compute_openai_embeddings(client, descriptions):
    embeddings = []
    for start in range(0, len(descriptions), OPENAI_EMBEDDING_BATCH):
        chunk = descriptions[start:start + OPENAI_EMBEDDING_BATCH]
        response = client.embeddings.create(input=chunk, model=OPENAI_EMBEDDING_MODEL)
        # The API tags each embedding with its input index; sort to guarantee the
        # output order matches the input order.
        embeddings.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    return embeddings


def main():
    parser = argparse.ArgumentParser(description="Compute per-IP endpoint embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the API to use for computing embeddings, either 'openai' or 'transformers' (default: 'openai' — for easy demos without a GPU; note the MCP server defaults to 'transformers', the preferred path on a GPU host).")
    parser.add_argument("--model", choices=list(PACKET_MODELS), default=DEFAULT_PACKET_MODEL, help=f"Local transformers model to use when --api transformers (default: '{DEFAULT_PACKET_MODEL}'). Add more in config.PACKET_MODELS.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--session", default="latest", help="Which capture session to profile: 'latest' (default), 'all' (every packet in the graph; timing still never crosses session boundaries), or a specific CAPTURE_ID from a capture run.")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    try:
        capture_id, session_ids = resolve_session(driver, args.database, args.session)
    except ValueError as e:
        reporter.error("ERROR", str(e))
        driver.close()
        return
    # The scope stamped on each ENDPOINT and reported back: a concrete session id, or
    # 'all' when unscoped ('all' requested, or a legacy graph with no CAPTURE nodes).
    session_scope = capture_id if capture_id else "all"
    reporter.info("CONFIG", f"Profiling session: {session_scope} ({len(session_ids)} session(s) in graph)")

    packets = fetch_packets(driver, args.database, capture_id)
    metadata = fetch_ip_metadata(driver, args.database)
    profiles = build_endpoint_profiles(packets, metadata)

    model_name = PACKET_MODELS[args.model] if args.api == "transformers" else OPENAI_EMBEDDING_MODEL
    embedding_strings = []
    embedding_tensors = []
    embedder = None

    processing_message = f"Embedding {len(profiles)} endpoint profiles using: {model_name}{f' ({device})' if args.api == 'transformers' else ''}"

    def render():
        return Group(
            render_info_panel("CONFIG", processing_message, CONSOLE),
            render_activity_panel("EMBEDDINGS(STR)", embedding_strings, CONSOLE),
            render_activity_panel("EMBEDDINGS(TENSOR)", [str(tensor) for tensor in embedding_tensors], CONSOLE)
        )

    try:
        if args.api == "transformers":
            embedder = SentenceTransformer(model_name, device=device, trust_remote_code=True)

        # Embed every profile in one batched pass (the panels then narrate the DB
        # writes). reporter.info first so a human sees progress during a long encode.
        reporter.info("CONFIG", processing_message)
        descriptions = [build_endpoint_description(profile) for profile in profiles]
        if not descriptions:
            embeddings = []
        elif args.api == "transformers":
            embeddings = compute_transformer_embeddings(descriptions, embedder)
        else:
            embeddings = compute_openai_embeddings(get_openai_client(), descriptions)

        # Old profiles go before new ones land — the ENDPOINT layer always reflects
        # exactly one compute run's scope (see clear_endpoints).
        clear_endpoints(driver, args.database)
        with reporter.activity(render) as update:
            for profile, description, embedding in zip(profiles, descriptions, embeddings):
                add_endpoint_to_database(profile, embedding, session_scope, driver, args.database)
                embedding_strings.append(description)
                embedding_tensors.append(embedding)
                update()

        reporter.result(
            {
                "database": args.database,
                "api": args.api,
                "model": model_name,
                "session": session_scope,
                "sessions_in_graph": len(session_ids),
                "endpoints_embedded": len(embedding_strings),
                "packets": len(packets),
            },
            summary=f"Embedded {len(embedding_strings)} endpoint profiles (one per IP) from {len(packets)} packets (session: {session_scope}) via {args.api} in: '{args.database}'",
        )
        return

    except Exception as e:
        reporter.error("ERROR", str(e))

    finally:
        if embedder is not None:
            del embedder
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        driver.close()

if __name__ == "__main__":
    main()
