import argparse
from rich.console import Group
import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModel
from jaws.config import (
    CONSOLE,
    DATABASE,
    PACKET_MODEL,
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
           p.SIZE AS size, p.PROTOCOL AS protocol
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

    profiles = []
    for ip in sorted(set(outbound) | set(inbound)):
        out = outbound.get(ip, {})
        inb = inbound.get(ip, {})
        meta = metadata.get(ip, {})
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
                    protocols=profile["protocols"])


device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
def compute_transformer_embedding(input, tokenizer, infer):
    inputs = tokenizer(input, return_tensors="pt", max_length=512, truncation=True).to(device)
    with torch.no_grad():
        outputs = infer(**inputs)
    last_hidden_states = outputs.last_hidden_state
    # jina-embeddings-v2-base-code is a purpose-built embedding model that expects mean pooling
    # over the real (non-padding) tokens, then L2-normalization for calibrated cosine geometry
    # in downstream clustering.
    mask = inputs["attention_mask"].unsqueeze(-1).to(last_hidden_states.dtype)
    summed = (last_hidden_states * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1e-9)
    mean_pooled = summed / counts
    normalized = torch.nn.functional.normalize(mean_pooled, p=2, dim=-1)
    embeddings = normalized.cpu().numpy().tolist()[0]
    return embeddings

def compute_openai_embedding(client, input):
    response = client.embeddings.create(input=input, model=OPENAI_EMBEDDING_MODEL)
    return response.data[0].embedding


def main():
    parser = argparse.ArgumentParser(description="Compute per-IP endpoint embeddings using either OpenAI or Transformers.")
    parser.add_argument("--api", choices=["openai", "transformers"], default="openai", help="Specify the API to use for computing embeddings, either 'openai' or 'transformers' (default: 'openai').")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    packets = fetch_packets(driver, args.database)
    metadata = fetch_ip_metadata(driver, args.database)
    profiles = build_endpoint_profiles(packets, metadata)

    embedding_strings = []
    embedding_tensors = []
    tokenizer = None
    infer = None

    processing_message = f"Embedding {len(profiles)} endpoint profiles using: {PACKET_MODEL +f'({device})' if args.api == 'transformers' else OPENAI_EMBEDDING_MODEL}"

    def render():
        return Group(
            render_info_panel("CONFIG", processing_message, CONSOLE),
            render_activity_panel("EMBEDDINGS(STR)", embedding_strings, CONSOLE),
            render_activity_panel("EMBEDDINGS(TENSOR)", [str(tensor) for tensor in embedding_tensors], CONSOLE)
        )

    try:
        if args.api == "transformers":
            tokenizer = AutoTokenizer.from_pretrained(PACKET_MODEL, trust_remote_code=True)
            infer = AutoModel.from_pretrained(PACKET_MODEL, trust_remote_code=True).to(device)

        with reporter.activity(render) as update:
            for profile in profiles:
                description = build_endpoint_description(profile)
                if args.api == "transformers":
                    embedding = compute_transformer_embedding(description, tokenizer, infer)
                else:
                    embedding = compute_openai_embedding(get_openai_client(), description)

                if embedding is not None:
                    add_endpoint_to_database(profile, embedding, driver, args.database)
                    embedding_strings.append(description)
                    embedding_tensors.append(embedding)
                    update()

        reporter.success("PROCESS COMPLETE", f"Embedded {len(embedding_strings)} endpoint profiles (one per IP) from {len(packets)} packets in: '{args.database}'")
        return

    except Exception as e:
        reporter.error("ERROR", str(e))

    finally:
        if infer is not None:
            del infer
        if tokenizer is not None:
            del tokenizer
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        driver.close()

if __name__ == "__main__":
    main()
