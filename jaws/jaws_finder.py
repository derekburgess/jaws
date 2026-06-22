import os
import argparse
import tempfile
import numpy as np
from sklearn.decomposition import PCA
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
from kneed import KneeLocator
import matplotlib.pyplot as plt
import plotille
from jaws.config import DATABASE, FINDER_ENDPOINT
from jaws.jaws_utils import (
    dbms_connection,
    Reporter
)


# Behavioral features blended with the text embedding so volume/fan-out anomalies
# (e.g. unusual outbound bytes) separate geometrically — text embeddings alone
# barely encode magnitude. Log-scaled (they span orders of magnitude) then
# standardized to unit variance, matching the standardized text components.
BASE_FEATURES = ["bytes_out", "bytes_in", "packets_out", "packets_in", "out_peers", "in_peers"]

# Derived shape features. Raw counts mostly re-flag the busiest host; ratios encode
# *shape* instead of volume, so they catch exfil (out >> in), beaconing (small constant
# packets), and low-fan-out chatter regardless of absolute size. The +1 denominators
# keep them finite when a direction is empty.
RATIO_FEATURES = {
    "bytes_out_in_ratio": lambda d: d["bytes_out"] / (d["bytes_in"] + 1.0),
    "packets_out_in_ratio": lambda d: d["packets_out"] / (d["packets_in"] + 1.0),
    "bytes_per_packet": lambda d: (d["bytes_out"] + d["bytes_in"]) / (d["packets_out"] + d["packets_in"] + 1.0),
    "bytes_per_peer": lambda d: d["bytes_out"] / (d["out_peers"] + 1.0),
}

# Temporal cadence features (set by jaws_compute). INTERVAL_CV — the coefficient of
# variation of inter-packet gaps — is the beaconing signal: a low CV means highly
# regular callbacks (C2-like), a high CV means bursty/human traffic. INTERVAL_MEAN is
# the typical period. Endpoints with too few packets carry None and are median-imputed
# below so they read as "average regularity" rather than as perfect beacons.
TIMING_FEATURES = ["interval_mean", "interval_cv"]

NUMERIC_FEATURE_COUNT = len(BASE_FEATURES) + len(RATIO_FEATURES) + len(TIMING_FEATURES)

# Column order of build_numeric_features (base, then ratios, then timing), so a
# robust-z column index maps back to the feature it scored.
NUMERIC_FEATURE_NAMES = BASE_FEATURES + list(RATIO_FEATURES) + TIMING_FEATURES

# Human-readable unit per numeric feature, surfaced in the result so the raw
# magnitudes aren't left unlabeled (bytes_out is bytes, interval_mean is seconds,
# the coefficient of variation and the out/in ratios are dimensionless).
FEATURE_UNITS = {
    "bytes_out": "bytes",
    "bytes_in": "bytes",
    "packets_out": "packets",
    "packets_in": "packets",
    "out_peers": "peers",
    "in_peers": "peers",
    "bytes_out_in_ratio": "ratio",
    "packets_out_in_ratio": "ratio",
    "bytes_per_packet": "bytes/packet",
    "bytes_per_peer": "bytes/peer",
    "interval_mean": "seconds",
    "interval_cv": "ratio",
}

# A feature must deviate by at least this robust-z to be cited as a reason an
# endpoint was anomalous. ~2.5 robust deviations is a clear departure from the pack
# without naming every minor wobble.
REASON_Z_THRESHOLD = 2.5


# The capture host's own IP is owned by this synthetic org (see initialize_schema).
# It is a structural hub — it talks to every peer, so it dominates behavioral clustering
# — and is excluded from the clustered set by default. Its outbound traffic still shows
# up as each remote endpoint's inbound, so outbound anomalies remain detectable.
LOCAL_ORG = "YOU ARE HERE"


# An endpoint's *_out / *_in counts are from ITS OWN perspective: bytes_out is what the
# endpoint sent, bytes_in what it received. Because the capture host is excluded from
# clustering by default, every scored endpoint is REMOTE — and a remote endpoint's
# bytes_out is data it sent TO the host, i.e. traffic the host DOWNLOADED, the opposite of
# exfiltration. Read naively, "bytes_out high" inverts the threat model (the tool's concern
# is outbound *from the host*). These maps re-state each directional feature in the capture
# host's frame so a reason can't be misread; the local host, when included, is its own frame
# (its perspective IS the host's). Keyed by flow: "out" = endpoint-as-source, "in" = -as-dest.
HOST_FRAME_REMOTE = {
    "out": "remote → host: traffic the capture host downloaded (NOT host exfil)",
    "in":  "host → remote: traffic the capture host sent to this IP (outbound-from-host signal)",
}
HOST_FRAME_LOCAL = {
    "out": "host → network: traffic the capture host sent out (outbound-from-host signal)",
    "in":  "network → host: traffic the capture host received (download)",
}

# Which directional flow each feature belongs to, for host-frame glossing. "out" features
# count the endpoint as source, "in" as destination; the out/in ratios and bytes_per_peer
# are out-dominant when high. Features absent here (bytes_per_packet, peers, timing) carry
# no host-relative direction and get no gloss.
FEATURE_FLOW = {
    "bytes_out": "out", "packets_out": "out",
    "bytes_in": "in", "packets_in": "in",
    "bytes_out_in_ratio": "out", "packets_out_in_ratio": "out",
    "bytes_per_peer": "out",
}


def host_relative_gloss(feature, is_local):
    """Defender-frame interpretation of a directional feature, or None if it has none.

    Disambiguates a flagged feature relative to the capture host so an agent doesn't read a
    remote endpoint's bytes_out (a host download) as exfiltration. See HOST_FRAME_* above.
    """
    flow = FEATURE_FLOW.get(feature)
    if flow is None:
        return None
    return (HOST_FRAME_LOCAL if is_local else HOST_FRAME_REMOTE)[flow]


def build_numeric_features(data):
    """Assemble the raw numeric matrix: base counts + derived ratios + timing.

    Timing columns may contain None (sparse endpoints); each is imputed with the
    median of its present values so missingness reads as neutral, not anomalous.
    Returns a float array of shape (n_endpoints, NUMERIC_FEATURE_COUNT).
    """
    base = [[float(d[f]) for f in BASE_FEATURES] for d in data]
    ratios = [[fn(d) for fn in RATIO_FEATURES.values()] for d in data]

    timing_cols = []
    for f in TIMING_FEATURES:
        col = [d.get(f) for d in data]
        present = [v for v in col if v is not None]
        median = float(np.median(present)) if present else 0.0
        timing_cols.append([float(v) if v is not None else median for v in col])
    timing = np.array(timing_cols).T if timing_cols else np.empty((len(data), 0))

    return np.hstack([np.array(base), np.array(ratios), timing])


def robust_z_scores(raw):
    """Per-column robust z-scores: (x - median) / (1.4826 * MAD), on log1p-scaled
    features.

    log1p first so multiplicative spread (bytes span orders of magnitude) reads on a
    single scale and one busy host doesn't swamp every column. median/MAD rather than
    mean/std so the location and scale aren't dragged toward the very outlier being
    measured. Where MAD is ~0 (e.g. many identical median-imputed timing values) it
    falls back to the standard deviation, and to 0 for a genuinely constant column —
    both avoid the div-by-zero infinities a naive MAD-z produces. Returns an array
    shaped like `raw`, columns aligned with NUMERIC_FEATURE_NAMES.
    """
    x = np.log1p(raw)
    median = np.median(x, axis=0)
    mad = np.median(np.abs(x - median), axis=0)
    scale = 1.4826 * mad
    scale = np.where(scale > 1e-9, scale, x.std(axis=0))
    z = np.zeros_like(x)
    usable = scale > 1e-9
    z[:, usable] = (x[:, usable] - median[usable]) / scale[usable]
    return z


def score_endpoints(data, clusters):
    """Attach a rankable anomaly score and reason codes to every endpoint.

    `anomaly_score` is the L2 norm of an endpoint's per-feature robust-z vector — its
    overall behavioral distance from the pack — so endpoints that deviate on several
    features outrank those that deviate on one, and the score exists even when DBSCAN
    flags nothing. `reasons` cites the features whose |robust-z| clears
    REASON_Z_THRESHOLD, each with its raw value, unit, and direction, turning
    'flagged' into 'flagged because bytes_out is far above the typical host'.
    `is_outlier` carries the DBSCAN verdict so the geometric flag and the
    interpretable score coexist. Returns the full list sorted by score descending.
    """
    raw = build_numeric_features(data)
    z = robust_z_scores(raw)
    scores = np.linalg.norm(z, axis=1)

    ranked = []
    for i, item in enumerate(data):
        # The clustered set is remote by default; a remote endpoint's *_out is data it sent
        # TO the host (a download). is_local flips the host-frame gloss for the capture host.
        is_local = item["org"] == LOCAL_ORG
        reasons = []
        for j, name in enumerate(NUMERIC_FEATURE_NAMES):
            zj = float(z[i, j])
            if abs(zj) >= REASON_Z_THRESHOLD:
                reason = {
                    "feature": name,
                    "value": round(float(raw[i, j]), 4),
                    "unit": FEATURE_UNITS[name],
                    "robust_z": round(zj, 2),
                    "direction": "high" if zj > 0 else "low",
                }
                # Defender-frame disambiguation so "bytes_out high" on a remote IP reads as
                # a host download, not exfil (omitted for non-directional features).
                gloss = host_relative_gloss(name, is_local)
                if gloss:
                    reason["host_relative"] = gloss
                reasons.append(reason)
        reasons.sort(key=lambda r: abs(r["robust_z"]), reverse=True)
        ranked.append({
            "ip_address": item["ip_address"],
            "org": item["org"],
            "hostname": item["hostname"],
            "location": item["location"],
            "bytes_out": item["bytes_out"],
            "packets_out": item["packets_out"],
            "bytes_in": item["bytes_in"],
            "packets_in": item["packets_in"],
            "interval_mean": item["interval_mean"],
            "interval_cv": item["interval_cv"],
            "anomaly_score": round(float(scores[i]), 4),
            "is_outlier": bool(clusters[i] == -1),
            "reasons": reasons,
        })
    ranked.sort(key=lambda e: e["anomaly_score"], reverse=True)
    return ranked


# The host-outbound view answers the tool's stated purpose directly — "unusual outbound
# traffic FROM the capture host." Clustering excludes the host (a structural hub), so its
# real outbound is scattered as small bytes_in across many remote endpoints and never
# dominates an anomaly_score; the remote-endpoint ranking is driven by inbound/download
# volume instead. This view re-centers on the host: for every destination the host SENT to,
# it measures the host's own upload (host as packet SOURCE, computed from raw packets so it
# isolates host→peer flow rather than the peer's total inbound from all sources) and ranks
# destinations on that distribution. upload_download_ratio >> 1 is the exfil shape.
HOST_OUTBOUND_FEATURES = ["upload_bytes", "upload_packets", "upload_download_ratio"]
HOST_OUTBOUND_UNITS = {
    "upload_bytes": "bytes",
    "upload_packets": "packets",
    "upload_download_ratio": "ratio",
}
# All three features are host→remote outbound, so every reason is unambiguously the
# outbound-from-host signal (no perspective flip needed — this view is already host-framed).
HOST_OUTBOUND_GLOSS = {
    "upload_bytes": "host → remote: data the capture host sent out (outbound from host)",
    "upload_packets": "host → remote: packets the capture host sent out (outbound from host)",
    "upload_download_ratio": "host sent more to this peer than it received (exfil-shaped when high)",
}


def score_host_outbound(rows):
    """Rank the host's outbound destinations on the host-upload distribution.

    `rows` is one dict per destination the host sent to (upload/download bytes & packets).
    Adds `upload_download_ratio`, an `outbound_score` (L2 norm of the robust-z vector over
    the host-upload features — same method as score_endpoints, but on the host's OWN
    outbound rather than a remote's perspective), and host-frame `reasons`. Returns the
    list sorted by outbound_score descending; an empty input yields an empty list.
    """
    if not rows:
        return []
    for r in rows:
        r["upload_download_ratio"] = r["upload_bytes"] / (r["download_bytes"] + 1.0)
    raw = np.array([[float(r[f]) for f in HOST_OUTBOUND_FEATURES] for r in rows])
    z = robust_z_scores(raw)
    scores = np.linalg.norm(z, axis=1)

    ranked = []
    for i, r in enumerate(rows):
        reasons = []
        for j, name in enumerate(HOST_OUTBOUND_FEATURES):
            zj = float(z[i, j])
            if abs(zj) >= REASON_Z_THRESHOLD:
                reasons.append({
                    "feature": name,
                    "value": round(float(raw[i, j]), 4),
                    "unit": HOST_OUTBOUND_UNITS[name],
                    "robust_z": round(zj, 2),
                    "direction": "high" if zj > 0 else "low",
                    "host_relative": HOST_OUTBOUND_GLOSS[name],
                })
        reasons.sort(key=lambda x: abs(x["robust_z"]), reverse=True)
        ranked.append({
            "ip_address": r["ip_address"],
            "org": r["org"],
            "hostname": r["hostname"],
            "location": r["location"],
            "upload_bytes": r["upload_bytes"],
            "upload_packets": r["upload_packets"],
            "download_bytes": r["download_bytes"],
            "download_packets": r["download_packets"],
            "upload_download_ratio": round(float(r["upload_download_ratio"]), 4),
            "outbound_score": round(float(scores[i]), 4),
            "reasons": reasons,
        })
    ranked.sort(key=lambda e: e["outbound_score"], reverse=True)
    return ranked


def _whole_number_formatter(val, chars, delta, left=False):
    # plotille label formatter: render axis tick labels as whole numbers instead of
    # full float precision (e.g. 4 rather than 3.73886505).
    s = f"{val:.0f}"
    return f"{s:<{chars}}" if left else f"{s:>{chars}}"


def new_plotille_figure():
    fig = plotille.Figure()
    fig.register_label_formatter(float, _whole_number_formatter)
    return fig


def build_feature_matrix(embeddings, data, components, whiten, feature_weight):
    """Combine text-embedding PCA components with standardized numeric features.

    The text block is kept at its natural PCA scale — deliberately NOT re-standardized,
    so when the descriptions are homogeneous (little real text variance) the text
    contributes little instead of having its noise amplified to unit scale. The numeric
    block is log-scaled and standardized to unit variance, and `feature_weight` scales it
    relative to the text (0.0 = embedding-only / original behavior, higher = more
    volume/fan-out influence). Returns (features_for_clustering, pca_object).
    """
    embeddings_array = np.array(embeddings)
    pca = PCA(n_components=components, whiten=whiten)
    text_block = pca.fit_transform(embeddings_array)

    if feature_weight <= 0:
        return text_block, pca

    raw = build_numeric_features(data)
    numeric_block = StandardScaler().fit_transform(np.log1p(raw))
    features = np.hstack([text_block, feature_weight * numeric_block])
    return features, pca


def fetch_data_for_dbscan(driver, database, include_local=False):
    query = """
    MATCH (endpoint:ENDPOINT)
    OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: endpoint.IP_ADDRESS})<-[:OWNERSHIP]-(org:ORGANIZATION)
    RETURN endpoint.IP_ADDRESS AS ip_address,
           COALESCE(endpoint.ORGANIZATION, org.ORGANIZATION, 'Unknown') AS org,
           COALESCE(endpoint.HOSTNAME, ip.HOSTNAME, 'Unknown') AS hostname,
           COALESCE(endpoint.LOCATION, ip.LOCATION, 'Unknown') AS location,
           endpoint.BYTES_OUT AS bytes_out,
           endpoint.PACKETS_OUT AS packets_out,
           endpoint.OUT_PEERS AS out_peers,
           endpoint.BYTES_IN AS bytes_in,
           endpoint.PACKETS_IN AS packets_in,
           endpoint.IN_PEERS AS in_peers,
           endpoint.INTERVAL_MEAN AS interval_mean,
           endpoint.INTERVAL_CV AS interval_cv,
           endpoint.EMBEDDING AS embedding
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        embeddings = []
        data = []
        excluded_local = 0
        for record in result:
            if record['embedding'] is not None:  # Only process endpoints with embeddings
                if not include_local and record['org'] == LOCAL_ORG:
                    excluded_local += 1
                    continue
                embeddings.append(np.array(record['embedding']))
                data.append({
                    'ip_address': record['ip_address'] or 'Unknown',
                    'org': record['org'] or 'Unknown',
                    'hostname': record['hostname'] or 'Unknown',
                    'location': record['location'] or 'Unknown',
                    'bytes_out': record['bytes_out'] or 0,
                    'packets_out': record['packets_out'] or 0,
                    'out_peers': record['out_peers'] or 0,
                    'bytes_in': record['bytes_in'] or 0,
                    'packets_in': record['packets_in'] or 0,
                    'in_peers': record['in_peers'] or 0,
                    # None when the endpoint had too few packets to time — kept as
                    # None so build_numeric_features median-imputes it.
                    'interval_mean': record['interval_mean'],
                    'interval_cv': record['interval_cv'],
                })
        return embeddings, data, excluded_local


def fetch_data_for_portsize(driver, database):
    query = """
    MATCH (src_port:PORT)-[:SENT]->(packet:PACKET)-[:RECEIVED]->(dst_port:PORT)
    RETURN packet.SIZE AS size, src_port.PORT AS src_port, dst_port.PORT AS dst_port
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        plot_data = [{'size': record['size'], 'src_port': record['src_port'], 'dst_port': record['dst_port']}
                     for record in result]
    return plot_data


def fetch_host_outbound(driver, database):
    """Aggregate the capture host's outbound traffic per destination, from raw packets.

    Finds the host IP(s) (owned by LOCAL_ORG), then for every peer the host exchanged
    packets with sums the host's upload (host as SOURCE) and download (host as
    DESTINATION) separately — so each row is the true host→peer flow, not the peer's total
    inbound from every source. Rows are restricted to peers the host actually sent to
    (upload_packets > 0). Returns (local_ips, rows); local_ips is empty when the host was
    never captured (e.g. an imported pcap with no local endpoint), in which case rows is
    empty too and the caller surfaces an empty host-outbound view rather than crashing.
    """
    local_query = """
    MATCH (org:ORGANIZATION {ORGANIZATION: $local_org})-[:OWNERSHIP]->(ip:IP_ADDRESS)
    RETURN collect(ip.IP_ADDRESS) AS local_ips
    """
    # peer = the non-local side of each packet; outbound = the host was the source. Group
    # by peer, split bytes/packets by direction, then join the peer's OSINT metadata.
    peer_query = """
    MATCH (p:PACKET)
    WHERE p.SRC_IP IN $local_ips OR p.DST_IP IN $local_ips
    WITH p,
         CASE WHEN p.SRC_IP IN $local_ips THEN p.DST_IP ELSE p.SRC_IP END AS peer,
         (p.SRC_IP IN $local_ips) AS outbound
    WHERE NOT peer IN $local_ips AND peer <> '0.0.0.0'
    WITH peer,
         sum(CASE WHEN outbound THEN p.SIZE ELSE 0 END) AS upload_bytes,
         sum(CASE WHEN outbound THEN 1 ELSE 0 END) AS upload_packets,
         sum(CASE WHEN NOT outbound THEN p.SIZE ELSE 0 END) AS download_bytes,
         sum(CASE WHEN NOT outbound THEN 1 ELSE 0 END) AS download_packets
    WHERE upload_packets > 0
    OPTIONAL MATCH (pip:IP_ADDRESS {IP_ADDRESS: peer})<-[:OWNERSHIP]-(porg:ORGANIZATION)
    RETURN peer AS ip_address,
           COALESCE(porg.ORGANIZATION, 'Unknown') AS org,
           COALESCE(pip.HOSTNAME, 'Unknown') AS hostname,
           COALESCE(pip.LOCATION, 'Unknown') AS location,
           upload_bytes, upload_packets, download_bytes, download_packets
    ORDER BY upload_bytes DESC
    """
    with driver.session(database=database) as session:
        local_ips = session.run(local_query, {"local_org": LOCAL_ORG}).single()["local_ips"]
        if not local_ips:
            return [], []
        rows = [record.data() for record in session.run(peer_query, {"local_ips": local_ips})]
    return local_ips, rows


def add_outlier_to_database(scored_list, flagged_list, driver, database):
    # Stamp an explicit OUTLIER verdict on every endpoint that was scored this run:
    # false by default, true for the flagged subset. This makes the property
    # three-state for readers (fetch_traffic / inspect_endpoint): true = flagged,
    # false = scored but clean, absent/null = never scored (anomaly_detection
    # hasn't run for it). Resetting to false first also clears stale true flags
    # from a previous run on the same graph.
    reset_query = """
    UNWIND $scored AS ip
    MATCH (endpoint:ENDPOINT {IP_ADDRESS: ip})
    SET endpoint.OUTLIER = false
    """
    flag_query = """
    UNWIND $outliers AS outlier
    MATCH (endpoint:ENDPOINT {IP_ADDRESS: outlier.ip_address})
    SET endpoint.OUTLIER = true
    """
    with driver.session(database=database) as session:
        session.run(reset_query, {'scored': [e['ip_address'] for e in scored_list]})
        session.run(flag_query, {'outliers': flagged_list})


def plot_size_over_ports(plot_data, jaws_finder_endpoint):
    plt.figure(num='Packet Size over Ports', figsize=(6, 4))
    for item in plot_data:
        plt.scatter(item['size'], item['src_port'], c=item['size'], cmap='winter', marker='^', s=50, alpha=0.1, zorder=10)
        plt.scatter(item['size'], item['dst_port'], c=item['size'], cmap='ocean', marker='^', s=50, alpha=0.1, zorder=10)

    plt.xlabel('SIZE', fontsize=8, color='#666666')
    plt.ylabel('PORT', fontsize=8, color='#666666')
    plt.legend(['SRC_PORT', 'DST_PORT'], loc='upper right', fontsize=8)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.grid(True, linewidth=0.5, color='#BEBEBE', alpha=0.5)
    plt.tight_layout()
    save_portsize = os.path.join(jaws_finder_endpoint, 'size_over_port.png')
    plt.savefig(save_portsize, dpi=90)

    portsize_plotille = new_plotille_figure()
    portsize_plotille.x_label = 'SIZE'
    portsize_plotille.y_label = 'PORT'
    portsize_plotille.color_mode = 'byte'
    portsize_plotille.width = 80
    portsize_plotille.height = 20
    portsize_plotille.set_x_limits(min_=0)
    portsize_plotille.set_y_limits(min_=0)
    for item in plot_data:
        portsize_plotille.scatter([item['size']], [item['src_port']], marker=">")
        portsize_plotille.scatter([item['size']], [item['dst_port']], marker="<")
    display_portsize = portsize_plotille.show(legend=False)
    print(display_portsize)


def plot_k_distances(sorted_k_distances, jaws_finder_endpoint):
    plt.figure(num='Sorted K-Distance', figsize=(6, 2))
    plt.plot(sorted_k_distances, color='seagreen', marker='o', linestyle='-', linewidth=0.5, alpha=0.8)
    plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    plt.xlabel('INDEX', fontsize=8, color='#666666')
    plt.ylabel('K-DISTANCE', fontsize=8, color='#666666')
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_kdistance = os.path.join(jaws_finder_endpoint, 'sorted_k_distance.png')
    plt.savefig(save_kdistance, dpi=90)

    kdistance_plotille = new_plotille_figure()
    kdistance_plotille.x_label = 'INDEX'
    kdistance_plotille.y_label = 'K-DISTANCE'
    kdistance_plotille.color_mode = 'byte'
    kdistance_plotille.width = 80
    kdistance_plotille.height = 20
    kdistance_plotille.set_x_limits(min_=0)
    kdistance_plotille.set_y_limits(min_=0)
    plotille_plot_x = list(range(len(sorted_k_distances)))
    kdistance_plotille.plot(plotille_plot_x, sorted_k_distances, marker="o", lc=40)
    display_kdistance = kdistance_plotille.show(legend=False)
    print(display_kdistance)


def recommend_eps(features, min_samples):
    """Knee-recommended DBSCAN eps for a feature space, with a median fallback.

    The same auto-eps procedure main() uses, factored out so the ablation can tune
    each condition's space by an identical rule (a fixed eps is meaningless across
    spaces of different scale/dimensionality).
    """
    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
    nearest_neighbors.fit(features)
    distances, _ = nearest_neighbors.kneighbors(features)
    sorted_k_distances = np.sort(distances[:, min_samples - 1])
    kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances,
                          curve='convex', direction='increasing')
    if kneedle.knee is not None:
        return float(sorted_k_distances[int(kneedle.knee)])
    return float(np.median(sorted_k_distances))


def run_ablation(embeddings, data, components, whiten, feature_weight):
    """Compare three feature-block conditions on the SAME endpoints, no DB writes.

    Answers "how much does the text embedding actually contribute vs the behavioral
    features?" by clustering each condition with an identical procedure (same
    min_samples, per-space knee-recommended eps) and reporting cluster quality
    (silhouette) plus which endpoints each flags (outlier sets + pairwise Jaccard).

      text-only    — the stored embedding alone (feature_weight 0)
      numeric-only — the 12 behavioral features alone, no embedding
      blended      — both, at the requested feature_weight

    Reuses embeddings already on the nodes — nothing is re-embedded.
    """
    text_only, _ = build_feature_matrix(embeddings, data, components, whiten, 0.0)
    numeric_only = StandardScaler().fit_transform(np.log1p(build_numeric_features(data)))
    blended, _ = build_feature_matrix(embeddings, data, components, whiten,
                                      feature_weight if feature_weight > 0 else 1.0)
    conditions = {"text-only": text_only, "numeric-only": numeric_only, "blended": blended}

    min_samples = 2 * components
    ips = [d["ip_address"] for d in data]
    summaries = {}
    outlier_sets = {}
    for name, feats in conditions.items():
        eps = recommend_eps(feats, min_samples)
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(feats)
        clustered = labels != -1
        n_clusters = len(set(labels[clustered]))
        # silhouette needs >=2 clusters and more clustered points than clusters.
        sil = None
        if n_clusters >= 2 and clustered.sum() > n_clusters:
            try:
                sil = float(silhouette_score(feats[clustered], labels[clustered]))
            except ValueError:
                sil = None
        outlier_sets[name] = {ips[i] for i in range(len(ips)) if labels[i] == -1}
        summaries[name] = {
            "dims": int(feats.shape[1]),
            "eps": round(eps, 4),
            "clusters": int(n_clusters),
            "outliers": int((~clustered).sum()),
            "silhouette": round(sil, 4) if sil is not None else None,
        }

    # Pairwise Jaccard of the flagged sets — high overlap means text is decorative
    # (numeric drives the flags); low overlap means the embedding changes outcomes.
    names = list(conditions)
    jaccard = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = outlier_sets[names[i]], outlier_sets[names[j]]
            union = a | b
            jaccard[f"{names[i]} vs {names[j]}"] = round(len(a & b) / len(union), 4) if union else 1.0

    return {
        "endpoints": len(data),
        "min_samples": min_samples,
        "conditions": summaries,
        "outlier_jaccard": jaccard,
        "outlier_sets": {k: sorted(v) for k, v in outlier_sets.items()},
    }


def format_ablation_table(result):
    """Render the ablation result as a fixed-width text table for the reporter."""
    header = f"{'CONDITION':<13}{'DIMS':>5}{'EPS':>9}{'CLUSTERS':>10}{'OUTLIERS':>10}{'SILHOUETTE':>12}"
    rows = [header]
    for name, s in result["conditions"].items():
        sil = "n/a" if s["silhouette"] is None else f"{s['silhouette']:.4f}"
        rows.append(f"{name:<13}{s['dims']:>5}{s['eps']:>9.4f}{s['clusters']:>10}{s['outliers']:>10}{sil:>12}")
    rows.append("")
    rows.append("Outlier-set agreement (Jaccard):")
    for pair, jac in result["outlier_jaccard"].items():
        rows.append(f"  {pair:<28}{jac:.4f}")
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(description="Perform DBSCAN clustering on embeddings fetched from the database.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--components", type=int, default=2, help="Number of PCA components to retain for clustering. The first 2 are always used for plotting, so values below 2 are clamped (default: 2).")
    parser.add_argument("--whiten", action="store_true", help="Whiten the PCA components (scale each to unit variance). Improves geometry with few strong components, but amplifies noise when retaining many low-variance components (default: off).")
    parser.add_argument("--eps", type=float, default=None, help="DBSCAN epsilon. When omitted, it is auto-recommended from the k-distance knee. The knee tends to overshoot on small/homogeneous datasets (folding everything into one cluster, 0 outliers) — pass a smaller value to surface more outliers.")
    parser.add_argument("--feature-weight", type=float, default=1.0, help="Influence of the behavioral numeric features (bytes/packets/peers, in & out) on clustering. The numeric block is standardized to unit variance and scaled by this weight; the text embedding keeps its natural scale. 0 = embedding-only (text/org/protocol structure), higher = more volume/fan-out influence to surface behavioral anomalies. Default 1.0.")
    parser.add_argument("--include-local", action="store_true", help="Include the capture host ('YOU ARE HERE') in the clustered set. Off by default — it is a structural hub that dominates clustering. Its outbound traffic still appears as each remote endpoint's inbound, so outbound anomalies are detectable without it.")
    parser.add_argument("--ablate", action="store_true", help="Ablation mode: cluster the same endpoints three ways — text-only (embedding alone), numeric-only (behavioral features alone), and blended — and report cluster quality (silhouette) and outlier-set agreement (Jaccard) to quantify how much the embedding contributes. Reuses stored embeddings, writes nothing, generates no plots.")
    args = parser.parse_args()
    reporter = Reporter()
    if args.components < 2:
        args.components = 2
    # Where plots are written. Falls back to a temp dir when JAWS_FINDER_ENDPOINT
    # is unset (e.g. a bare MCP/headless run) so saving never crashes, and the
    # directory is created if missing.
    endpoint = FINDER_ENDPOINT or os.path.join(tempfile.gettempdir(), "jaws")
    os.makedirs(endpoint, exist_ok=True)
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    embeddings, data, excluded_local = fetch_data_for_dbscan(driver, args.database, args.include_local)
    if excluded_local:
        reporter.info("CONFIG", f"Excluded the local host ('{LOCAL_ORG}') from clustering. Pass --include-local to keep it.")

    if args.ablate:
        min_samples = 2 * args.components
        if len(data) < min_samples:
            reporter.error("ERROR", f"Ablation needs at least {min_samples} embedded endpoints (have {len(data)}). Capture more traffic or lower --components.")
            driver.close()
            return
        result = run_ablation(embeddings, data, args.components, args.whiten, args.feature_weight)
        reporter.info("ABLATION", format_ablation_table(result))
        reporter.result(
            result,
            summary=f"Ablation over {result['endpoints']} endpoints: text-only vs numeric-only vs blended (no DB writes).",
        )
        driver.close()
        return

    plot_data = fetch_data_for_portsize(driver, args.database)
    portsize_info_message = "The below plot shows the packet size over ports.\nIt is useful for identifying ports that are sending or receiving large amounts of data."
    if not reporter.agent:
        reporter.info("INFO", portsize_info_message)
        plot_size_over_ports(plot_data, endpoint)

    feature_info_message = (
        f"Reducing {len(embeddings)} endpoint embeddings to {args.components} PCA dimensions"
        + (f", blended with {NUMERIC_FEATURE_COUNT} behavioral features "
           f"(counts, shape ratios & timing; weight {args.feature_weight})."
           if args.feature_weight > 0 else " (behavioral features disabled).")
    )
    reporter.info("INFO", feature_info_message)

    # Clustering runs on `features` (standardized text PCA components, optionally blended
    # with standardized behavioral features). `plot_xy` is a 2D projection of that same
    # space, so the scatter plot reflects what was actually clustered.
    features, pca = build_feature_matrix(embeddings, data, args.components, args.whiten, args.feature_weight)
    plot_xy = PCA(n_components=2).fit_transform(features) if features.shape[1] > 2 else features

    explained = pca.explained_variance_ratio_
    per_component = ", ".join(f"PC{i + 1} {v:.2%}" for i, v in enumerate(explained))
    explained_variance_message = (
        f"PCA explained variance ratio: {per_component} "
        f"(total {explained.sum():.2%} of variance retained in {args.components} dimensions)."
    )
    reporter.info("INFO", explained_variance_message)

    kdistance_info_message = "Measuring K-Distance. This is used to determine the optimal epsilon value\nfor DBSCAN(Density-Based Spatial Clustering of Applications with Noise)."
    reporter.info("INFO", kdistance_info_message)

    min_samples = 2 * args.components
    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
    nearest_neighbors.fit(features)
    distances, _ = nearest_neighbors.kneighbors(features)
    k_distances = distances[:, min_samples - 1]
    sorted_k_distances = np.sort(k_distances)
    if not reporter.agent:
        plot_k_distances(sorted_k_distances, endpoint)

    kneed_info_message = "Using Kneed to recommend EPS.\nKneed is a library that helps us find the knee point in the K-Distance plot."
    reporter.info("INFO", kneed_info_message)

    knee_index = None
    if args.eps is not None:
        # Explicit override — skip the knee recommendation and the interactive prompt.
        eps_value = args.eps
        eps_source = "override"
        reporter.info("CONFIG", f"Using provided EPS: {eps_value}")
    else:
        eps_source = "auto"
        kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
        knee_index = int(kneedle.knee) if kneedle.knee is not None else None
        if knee_index is not None:
            eps_value = sorted_k_distances[knee_index]
            reporter.info("INFO", f"Knee point found at index: {knee_index}")
        else:
            reporter.info("INFO", "Knee point not found. Using default EPS.")
            eps_value = np.median(sorted_k_distances)

        if not reporter.agent:
            user_input = input(f"[RECOMMENDED EPS] {eps_value:.2f} | Press ENTER to accept, or provide a value: ")
            if user_input:
                try:
                    eps_value = float(user_input)
                    eps_source = "manual"
                except ValueError:
                    reporter.error("ERROR", "Invalid input. Using the recommended EPS value.")
            reporter.info("INFORMATION", "Matplotlib plots will be generated after passing an EPS value.")
        else:
            reporter.info("CONFIG", "Skipping user input and passing the recommended EPS value.")

    dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
    clusters = dbscan.fit_predict(features)

    if not reporter.agent:
        reporter.info("INFO", "The below plot shows the PCA/DBSCAN outliers, in red, from the embeddings.\nAdditionally, embedding clusters are shown to help understand how outliers are distributed amongst noise.")

    plt.figure(num=f'PCA/DBSCAN Outliers from Embeddings | n_components: {args.components}, min_samples: {min_samples}, eps: {eps_value}', figsize=(8, 7))
    clustered_indices = clusters != -1
    plt.scatter(plot_xy[clustered_indices, 0], plot_xy[clustered_indices, 1], 
                c=clusters[clustered_indices], cmap='winter', edgecolors='none', marker='^', s=50, alpha=0.1, zorder=2)

    outlier_indices = clusters == -1
    plt.scatter(plot_xy[outlier_indices, 0], plot_xy[outlier_indices, 1], 
                color='red', marker='o', s=50, label='Outliers', alpha=0.8, zorder=10)

    for i, item in enumerate(data):
        annotation_text = f"{item['ip_address']}\n{item['org']}\n{item['hostname']}\n{item['location']}\nout {item['bytes_out']}B/{item['packets_out']}p | in {item['bytes_in']}B/{item['packets_in']}p"
        if clusters[i] == -1:
            # Outlier
            bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#333333', edgecolor='none', alpha=0.9)
            plt.annotate(annotation_text, 
                        (plot_xy[i, 0], plot_xy[i, 1]), 
                        fontsize=6,
                        color='white', 
                        bbox=bbox_style,
                        horizontalalignment='center',
                        verticalalignment='bottom',
                        xytext=(0,10),
                        textcoords='offset points',
                        alpha=0.9,
                        zorder=10)
        else:
            # Non-Outlier
            bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#BEBEBE', edgecolor='none', alpha=0.5)
            plt.annotate(annotation_text, 
                        (plot_xy[i, 0], plot_xy[i, 1]), 
                        fontsize=6,
                        color='#666666',
                        bbox=bbox_style,
                        horizontalalignment='center',
                        verticalalignment='bottom',
                        xytext=(0,10),
                        textcoords='offset points',
                        alpha=0.8,
                        zorder=1)

    plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_outliers = os.path.join(endpoint, 'pca_dbscan_outliers.png')
    plt.savefig(save_outliers, dpi=90)

    outlier_plotille = new_plotille_figure()
    outlier_plotille.color_mode = 'byte'
    outlier_plotille.width = 80
    outlier_plotille.height = 20
    clustered_indices_pc1 = plot_xy[clustered_indices, 0]
    clustered_indices_pc2 = plot_xy[clustered_indices, 1]
    outlier_indices_pc1 = plot_xy[outlier_indices, 0]
    outlier_indices_pc2 = plot_xy[outlier_indices, 1]
    outlier_plotille.scatter(clustered_indices_pc1, clustered_indices_pc2, marker="^")
    outlier_plotille.scatter(outlier_indices_pc1, outlier_indices_pc2, marker="o")
    display_outlier = outlier_plotille.show(legend=False)

    if not reporter.agent:
        reporter.raw(display_outlier)

    # Every endpoint gets a rankable anomaly score and reason codes, sorted most
    # anomalous first; `is_outlier` marks the DBSCAN-flagged ones. Returning the full
    # ranked list (not just the flagged subset) means there is always something to
    # triage — a 0-outlier DBSCAN run still yields a ranking.
    ranked_endpoints = score_endpoints(data, clusters)
    flagged = [e for e in ranked_endpoints if e["is_outlier"]]

    add_outlier_to_database(ranked_endpoints, flagged, driver, args.database)

    # First-class host-outbound view: outbound FROM the capture host, per destination,
    # isolated from raw packets (host as source). The remote-endpoint ranking above is
    # dominated by inbound/download volume and structurally demotes the host's own outbound
    # (the documented purpose) — this re-centers on it, independent of clustering and the
    # --include-local flag. Empty when the host wasn't captured (e.g. an imported pcap).
    local_ips, host_rows = fetch_host_outbound(driver, args.database)
    host_destinations = score_host_outbound(host_rows)
    host_flagged = [d for d in host_destinations if d["reasons"]]
    if not reporter.agent and host_destinations:
        top = host_destinations[0]
        reporter.info("HOST OUTBOUND",
                      f"Top outbound destination from the host: {top['ip_address']} ({top['org']}) "
                      f"— {top['upload_bytes']} bytes up / {top['download_bytes']} down "
                      f"(score {top['outbound_score']}). {len(host_flagged)} destination(s) flagged.")

    # Structured result with the clustering diagnostics folded in (so the caller gets
    # the useful numbers as fields, not prose). `endpoints` is the full ranked list;
    # an empty `flagged` set (outliers_flagged == 0) means DBSCAN flagged nothing —
    # not a failure — but anomaly_score still ranks every endpoint. `units` labels the
    # raw magnitudes; `reason_z_threshold` is the robust-z cutoff for citing a feature.
    result = {
        "endpoints_clustered": len(data),
        "outliers_flagged": len(flagged),
        "excluded_local": excluded_local,
        "eps": round(float(eps_value), 4),
        "eps_source": eps_source,
        "knee_index": knee_index,
        "min_samples": min_samples,
        "components": args.components,
        "feature_weight": args.feature_weight,
        "pca_variance": [round(float(v), 4) for v in explained],
        "pca_variance_total": round(float(explained.sum()), 4),
        "units": FEATURE_UNITS,
        "reason_z_threshold": REASON_Z_THRESHOLD,
        # Counts are from each endpoint's OWN perspective. With the capture host excluded
        # (the default), endpoints are remote, so a remote IP's bytes_out/packets_out is
        # traffic it sent TO the host (a host download) and bytes_in/packets_in is traffic
        # the host sent to it (the outbound-from-host signal). Each directional reason
        # carries a `host_relative` gloss; don't read a high bytes_out on a remote IP as exfil.
        "perspective": (
            "Endpoint counts are from the endpoint's own perspective. Scored endpoints are "
            "remote (capture host excluded by default): bytes_out/packets_out = traffic the "
            "remote IP sent to the host (host download); bytes_in/packets_in = traffic the host "
            "sent to it (outbound from host). See each reason's host_relative field. The "
            "host_outbound section re-frames the same capture from the host's perspective."
        ),
        "endpoints": ranked_endpoints,
        # The defender-frame counterpart to `endpoints`: the capture host's OWN outbound,
        # per destination, ranked on the host-upload distribution. Directly serves the
        # tool's purpose ("unusual outbound from the local host"), which the remote-endpoint
        # ranking demotes. `local_ips` is empty when no local host was captured.
        "host_outbound": {
            "local_ips": local_ips,
            "destinations_ranked": len(host_destinations),
            "flagged": len(host_flagged),
            "units": HOST_OUTBOUND_UNITS,
            "note": (
                "Outbound FROM the capture host, per destination, isolated from raw packets "
                "(host as source, so upload_bytes is host→peer, not the peer's total inbound). "
                "`outbound_score` ranks destinations on the host-upload distribution; "
                "upload_download_ratio high = exfil-shaped (host sent far more than it received). "
                "Use this, not the download-dominated `endpoints` ranking, to judge host exfil/beaconing."
            ),
            "destinations": host_destinations,
        },
    }

    if not reporter.agent:
        plt.show()

    reporter.result(result, summary=f"Clustered {len(data)} endpoints (per IP); {len(flagged)} outlier(s) flagged, all ranked by anomaly_score; {len(host_flagged)} host-outbound destination(s) flagged. Plots saved to: {endpoint}")

    driver.close()

if __name__ == "__main__":
    main()