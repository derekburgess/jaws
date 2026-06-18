import os
import argparse
import tempfile
import numpy as np
from sklearn.decomposition import PCA
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler
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
NUMERIC_FEATURES = ["bytes_out", "bytes_in", "packets_out", "packets_in", "out_peers", "in_peers"]

# The capture host's own IP is owned by this synthetic org (see initialize_schema).
# It is a structural hub — it talks to every peer, so it dominates behavioral clustering
# — and is excluded from the clustered set by default. Its outbound traffic still shows
# up as each remote endpoint's inbound, so outbound anomalies remain detectable.
LOCAL_ORG = "YOU ARE HERE"


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

    raw = np.array([[float(d[f]) for f in NUMERIC_FEATURES] for d in data])
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


def add_outlier_to_database(outlier_list, driver, database):
    query = """
    UNWIND $outliers AS outlier
    MATCH (endpoint:ENDPOINT {IP_ADDRESS: outlier.ip_address})
    SET endpoint.OUTLIER = true
    """
    parameters = {'outliers': outlier_list}
    with driver.session(database=database) as session:
        session.run(query, parameters)


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

    portsize_plotille = plotille.Figure()
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

    kdistance_plotille = plotille.Figure()
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


def main():
    parser = argparse.ArgumentParser(description="Perform DBSCAN clustering on embeddings fetched from the database.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--components", type=int, default=2, help="Number of PCA components to retain for clustering. The first 2 are always used for plotting, so values below 2 are clamped (default: 2).")
    parser.add_argument("--whiten", action="store_true", help="Whiten the PCA components (scale each to unit variance). Improves geometry with few strong components, but amplifies noise when retaining many low-variance components (default: off).")
    parser.add_argument("--eps", type=float, default=None, help="DBSCAN epsilon. When omitted, it is auto-recommended from the k-distance knee. The knee tends to overshoot on small/homogeneous datasets (folding everything into one cluster, 0 outliers) — pass a smaller value to surface more outliers.")
    parser.add_argument("--feature-weight", type=float, default=1.0, help="Influence of the behavioral numeric features (bytes/packets/peers, in & out) on clustering. The numeric block is standardized to unit variance and scaled by this weight; the text embedding keeps its natural scale. 0 = embedding-only (text/org/protocol structure), higher = more volume/fan-out influence to surface behavioral anomalies. Default 1.0.")
    parser.add_argument("--include-local", action="store_true", help="Include the capture host ('YOU ARE HERE') in the clustered set. Off by default — it is a structural hub that dominates clustering. Its outbound traffic still appears as each remote endpoint's inbound, so outbound anomalies are detectable without it.")
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
    plot_data = fetch_data_for_portsize(driver, args.database)
    portsize_info_message = "The below plot shows the packet size over ports.\nIt is useful for identifying ports that are sending or receiving large amounts of data."
    if not reporter.agent:
        reporter.info("INFO", portsize_info_message)
        plot_size_over_ports(plot_data, endpoint)

    feature_info_message = (
        f"Reducing {len(embeddings)} endpoint embeddings to {args.components} PCA dimensions"
        + (f", blended with {len(NUMERIC_FEATURES)} behavioral features (weight {args.feature_weight})."
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
            user_input = input(f"[RECOMMENDED EPS] {eps_value} | Press ENTER to accept, or provide a value: ")
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

    outlier_plotille = plotille.Figure()
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

    outlier_data = [
        {
            'ip_address': item['ip_address'],
            'org': item['org'],
            'hostname': item['hostname'],
            'location': item['location'],
            'bytes_out': item['bytes_out'],
            'packets_out': item['packets_out'],
            'bytes_in': item['bytes_in'],
            'packets_in': item['packets_in'],
        } for i, item in enumerate(data) if clusters[i] == -1
    ]

    add_outlier_to_database(outlier_data, driver, args.database)

    # Structured result with the clustering diagnostics folded in (so the caller gets
    # the useful numbers as fields, not prose). An empty `outliers` list means zero
    # outliers were found — not a failure.
    result = {
        "endpoints_clustered": len(data),
        "outliers_flagged": len(outlier_data),
        "excluded_local": excluded_local,
        "eps": round(float(eps_value), 4),
        "eps_source": eps_source,
        "knee_index": knee_index,
        "min_samples": min_samples,
        "components": args.components,
        "feature_weight": args.feature_weight,
        "pca_variance": [round(float(v), 4) for v in explained],
        "pca_variance_total": round(float(explained.sum()), 4),
        "outliers": outlier_data,
    }

    if not reporter.agent:
        plt.show()

    reporter.result(result, summary=f"Clustered {len(data)} endpoints (per IP); {len(outlier_data)} outlier(s) flagged. Plots saved to: {endpoint}")

    driver.close()

if __name__ == "__main__":
    main()