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
    render_activity_panel
)


def fetch_packets(driver, database):
    # PACKET nodes carry the 5-tuple + size as properties, so per-IP aggregation
    # reads straight off them (one scan) — no traversal needed.
    query = """
    MATCH (p:PACKET)
    RETURN p.SRC_IP AS src_ip, p.DST_IP AS dst_ip,
           p.SRC_PORT AS src_port, p.DST_PORT AS dst_port,
           p.SIZE AS size, p.PROTOCOL AS protocol,
           p.TIMESTAMP.epochMillis AS ts_ms
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        df = pd.DataFrame([record.data() for record in result])
    return df


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


# Minimum packets in an endpoint's stream before its timing is meaningful. Two
# packets give one interval (no variance); three give two intervals — the floor for
# a coefficient of variation. Below this, timing is left undefined (None) and the
# finder imputes it, so sparse endpoints aren't mistaken for perfectly regular beacons.
MIN_TIMING_PACKETS = 3


def endpoint_timing(ts_ms):
    """Inter-packet cadence for one endpoint's combined packet stream.

    Returns (interval_mean, interval_cv) in seconds, or (None, None) when there are
    too few packets to assess. `interval_cv` (std/mean of inter-packet gaps) is the
    beaconing signal: a low CV means highly regular callbacks (C2-like), a high CV
    means bursty/human traffic. `interval_mean` is the typical gap, i.e. the period.
    """
    ts = np.sort(np.asarray(ts_ms, dtype=float)) / 1000.0
    ts = ts[~np.isnan(ts)]
    if len(ts) < MIN_TIMING_PACKETS:
        return None, None
    diffs = np.diff(ts)
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
    has_ts = "ts_ms" in packets.columns
    timing = {}
    if has_ts:
        for ip in set(packets["src_ip"]) | set(packets["dst_ip"]):
            mask = (packets["src_ip"] == ip) | (packets["dst_ip"] == ip)
            timing[ip] = endpoint_timing(packets.loc[mask, "ts_ms"])

    profiles = []
    for ip in sorted(set(outbound) | set(inbound)):
        out = outbound.get(ip, {})
        inb = inbound.get(ip, {})
        meta = metadata.get(ip, {})
        interval_mean, interval_cv = timing.get(ip, (None, None))
        profiles.append({
            "ip_address": ip,
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
        f"IP: {p['ip_address']} | Organization: {p['org']} | Hostname: {p['hostname']} | Location: {p['location']}\n"
        f"Outbound: {p['bytes_out']} bytes, {p['packets_out']} packets to {p['out_peers']} peers | Ports: {p['out_ports']}\n"
        f"Inbound: {p['bytes_in']} bytes, {p['packets_in']} packets from {p['in_peers']} peers | Ports: {p['in_ports']}\n"
        f"Protocols: {p['protocols']}\n"
    )


def add_endpoint_to_database(profile, embedding, driver, database):
    query = """
    MATCH (ip:IP_ADDRESS {IP_ADDRESS: $ip_address})
    MERGE (ip)-[:PROFILE]->(endpoint:ENDPOINT {IP_ADDRESS: $ip_address})
    SET endpoint.EMBEDDING = $embedding,
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
                    org=profile["org"], hostname=profile["hostname"], location=profile["location"],
                    bytes_out=profile["bytes_out"], packets_out=profile["packets_out"],
                    out_peers=profile["out_peers"], out_ports=profile["out_ports"],
                    bytes_in=profile["bytes_in"], packets_in=profile["packets_in"],
                    in_peers=profile["in_peers"], in_ports=profile["in_ports"],
                    protocols=profile["protocols"],
                    interval_mean=profile.get("interval_mean"),
                    interval_cv=profile.get("interval_cv"))


device = "cuda" if torch.cuda.is_available() else "cpu"
def compute_transformer_embedding(input, embedder):
    # sentence-transformers reads each model's own pooling config and applies it; with
    # normalize_embeddings it L2-normalizes for calibrated cosine geometry downstream.
    # One function works for any model in PACKET_MODELS — no per-model code.
    return embedder.encode(input, normalize_embeddings=True).tolist()

def compute_openai_embedding(client, input):
    response = client.embeddings.create(input=input, model=OPENAI_EMBEDDING_MODEL)
    return response.data[0].embedding


def main():
    parser = argparse.ArgumentParser(description="Compute per-IP endpoint embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the API to use for computing embeddings, either 'openai' or 'transformers' (default: 'openai').")
    parser.add_argument("--model", choices=list(PACKET_MODELS), default=DEFAULT_PACKET_MODEL, help=f"Local transformers model to use when --api transformers (default: '{DEFAULT_PACKET_MODEL}'). Add more in config.PACKET_MODELS.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    packets = fetch_packets(driver, args.database)
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

        with reporter.activity(render) as update:
            for profile in profiles:
                description = build_endpoint_description(profile)
                if args.api == "transformers":
                    embedding = compute_transformer_embedding(description, embedder)
                else:
                    embedding = compute_openai_embedding(get_openai_client(), description)

                if embedding is not None:
                    add_endpoint_to_database(profile, embedding, driver, args.database)
                    embedding_strings.append(description)
                    embedding_tensors.append(embedding)
                    update()

        reporter.result(
            {
                "database": args.database,
                "api": args.api,
                "model": model_name,
                "endpoints_embedded": len(embedding_strings),
                "packets": len(packets),
            },
            summary=f"Embedded {len(embedding_strings)} endpoint profiles (one per IP) from {len(packets)} packets via {args.api} in: '{args.database}'",
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
