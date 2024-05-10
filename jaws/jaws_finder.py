import os
import random
import argparse
from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import plotille


def fetch_data(driver, database, type):
    if type == "org":
        embedding = "org.org_embedding"
        where_clause = "org IS NOT NULL AND org.org_embedding IS NOT NULL"
    else:
        embedding = "p.packet_embedding"
        where_clause = "p.packet_embedding IS NOT NULL"

    query = f"""
    MATCH (src:SRC_IP)-[p:PACKET]->(dst:DST_IP)
    MATCH (src)-[:OWNERSHIP]->(org:ORGANIZATION)
    WHERE {where_clause}
    RETURN src.src_address AS src_ip, 
        src.src_port AS src_port,
        dst.dst_address AS dst_ip,  
        dst.dst_port AS dst_port, 
        p.protocol AS protocol,
        p.size AS size,
        org.org AS org,
        org.hostname AS hostname,
        org.location AS location,
        {embedding} AS embedding
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        embeddings = []
        data = []
        for record in result:
            embeddings.append(np.array(record['embedding']))
            data.append({
                'src_ip': record['src_ip'],
                'dst_ip': record['dst_ip'],
                'src_port': record['src_port'],
                'dst_port': record['dst_port'],
                'protocol': record['protocol'],
                'size': record['size'],
                'org': record['org'],
                'hostname': record['hostname'],
                'location': record['location'],
            })
        return embeddings, data


def update_neo4j(outlier_list, driver, database):
    query = """
    UNWIND $outliers AS outlier
    MATCH (org:ORGANIZATION {org: outlier.org})
    MERGE (outlierNode:OUTLIER {
        src_ip: outlier.src_ip,
        src_port: outlier.src_port,
        dst_ip: outlier.dst_ip,
        dst_port: outlier.dst_port,
        size: outlier.size,
        protocol: outlier.protocol,
        location: outlier.location
    })
    MERGE (org)-[:ANOMALY]->(outlierNode)
    """
    parameters = {'outliers': outlier_list}
    with driver.session(database=database) as session:
        session.run(query, parameters)


def main():
    parser = argparse.ArgumentParser(description="Perform DBSCAN clustering on embeddings fetched from Neo4j.")
    parser.add_argument("--type", choices=["packet", "org"], default="packet",
                        help="Specify the packet string type to pass (default: packet)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")
    
    args = parser.parse_args()
    uri = os.getenv("NEO4J_URI")
    username = os.getenv("NEO4J_USERNAME")
    password = os.getenv("NEO4J_PASSWORD")
    driver = GraphDatabase.driver(uri, auth=(username, password))
    embeddings, data = fetch_data(driver, args.database, args.type)
    jaws_finder_endpoint = os.getenv("JAWS_FINDER_ENDPOINT")

    print("\nPlotting the Size of Packets over Ports", "\n")

    # Plot the size of packets over source ports
    plt.figure(num='Packet Size over SRC/DST Port', figsize=(6, 4))

    for i, item in enumerate(data):
        size = item.get('size')
        src_port = item.get('src_port')
        dst_port = item.get('dst_port')
        plt.scatter(size, src_port, c=size, cmap='winter', marker='^', s=50, alpha=0.1, zorder=10)
        plt.scatter(size, dst_port, c=size, cmap='ocean', marker='^', s=50, alpha=0.1, zorder=10)

    plt.xlabel('SIZE', fontsize=8, color='#666666')
    plt.ylabel('SRC_PORT / DST_PORT', fontsize=8, color='#666666')
    plt.legend(['SRC_PORT', 'DST_PORT'], loc='upper right', fontsize=8)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.grid(True, linewidth=0.5, color='#BEBEBE', alpha=0.5)
    plt.tight_layout()
    save_portsize = os.path.join(jaws_finder_endpoint, 'size_over_port.png')
    plt.savefig(save_portsize, dpi=300)

    # Plot the Size and Port scatter using Plotille
    portsize_plotille = plotille.Figure()
    portsize_plotille.x_label = 'SIZE'
    portsize_plotille.y_label = 'PORT'
    portsize_plotille.color_mode = 'byte'
    portsize_plotille.width = 80
    portsize_plotille.height = 20
    portsize_plotille.set_x_limits(min_=0)
    portsize_plotille.set_y_limits(min_=0)
    for item in data:
        size = item.get('size')
        src_port = item.get('src_port')
        dst_port = item.get('dst_port')
        portsize_plotille.scatter([size], [src_port], marker=">")
        portsize_plotille.scatter([size], [dst_port], marker="<")
    print(portsize_plotille.show(legend=False))

    embeddings_scaled = StandardScaler().fit_transform(embeddings)
    print(f"\nPerforming PCA on {len(embeddings_scaled)} Embeddings")
    pca = PCA(n_components=2)
    principal_components = pca.fit_transform(embeddings_scaled)

    print("Measuring K-Distance", "\n")
    min_samples = 2
    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)

    if args.type == "packet":
        nearest_neighbors.fit(principal_components)
        distances, _ = nearest_neighbors.kneighbors(principal_components)
    elif args.type == "org":
        nearest_neighbors.fit(embeddings_scaled)
        distances, _ = nearest_neighbors.kneighbors(embeddings_scaled)

    k_distances = distances[:, min_samples - 1]
    sorted_k_distances = np.sort(k_distances)
    
    # Plot the sorted K-Distance
    plt.figure(num='Sorted K-Distance', figsize=(6, 2))
    plt.plot(sorted_k_distances, color='seagreen', marker='o', linestyle='-', linewidth=0.5, alpha=0.8)
    plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    plt.xlabel('INDEX', fontsize=8, color='#666666')
    plt.ylabel('K-DISTANCE', fontsize=8, color='#666666')
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_kdistance = os.path.join(jaws_finder_endpoint, 'sorted_k_distance.png')
    plt.savefig(save_kdistance, dpi=300)

    # Plot the sorted K-Distance using Plotille
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
    print(kdistance_plotille.show(legend=False))

    print("\nUsing Kneed to recommend EPS")
    kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
    eps_value = sorted_k_distances[kneedle.knee]
    eps_value = float(eps_value)
    user_input = input(f"Recommended EPS: {eps_value} | Press ENTER to accept, or provide a value: ")
    if user_input:
        try:
            eps_value = float(user_input)
        except ValueError:
            print("Invalid input. Using the recommended EPS value...")

    print(f"Using EPS: {eps_value}", "\n")
    dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
    clusters = dbscan.fit_predict(principal_components)

    # Plot the PCA/DBSCAN Outliers
    plt.figure(num=f'PCA/DBSCAN Outliers from Embeddings | n_components/samples: 2, eps: {eps_value}', figsize=(8, 7))
    clustered_indices = clusters != -1
    plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                        c=clusters[clustered_indices], cmap='winter', edgecolors='none', marker='^', s=50, alpha=0.1, zorder=2)

    outlier_indices = clusters == -1
    plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
                color='red', marker='o', s=50, label='Outliers', alpha=0.8, zorder=10)

    position_options = {
        'top': {'offset': (0, 10), 'horizontalalignment': 'center', 'verticalalignment': 'bottom'},
        'bottom': {'offset': (0, -10), 'horizontalalignment': 'center', 'verticalalignment': 'top'},
        'right': {'offset': (10, 0), 'horizontalalignment': 'left', 'verticalalignment': 'center'},
        'left': {'offset': (-10, 0), 'horizontalalignment': 'right', 'verticalalignment': 'center'}
    }

    for i, item in enumerate(data):
        if clusters[i] != -1:
            # Non-Outlier
            annotation_text = f"{item.get('org')}\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} -> {item.get('dst_ip')}:{item.get('dst_port')}\n{item.get('size')} ({item.get('protocol')})"
            bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#BEBEBE', edgecolor='none', alpha=0.1)
            label_position_key = random.choice(list(position_options.keys()))
            label_position = position_options[label_position_key]
            
            plt.annotate(annotation_text, 
                        (principal_components[i, 0], principal_components[i, 1]), 
                        fontsize=6,
                        color='#999999',
                        bbox=bbox_style,
                        horizontalalignment=label_position['horizontalalignment'],
                        verticalalignment=label_position['verticalalignment'],
                        xytext=label_position['offset'],
                        textcoords='offset points',
                        alpha=0.8,
                        zorder=1)
        else:
            # Outlier
            annotation_text = f"{item.get('org')}\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} -> {item.get('dst_ip')}:{item.get('dst_port')}\n{item.get('size')} ({item.get('protocol')})"
            bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#333333', edgecolor='none', alpha=0.9)
            
            plt.annotate(annotation_text, 
                        (principal_components[i, 0], principal_components[i, 1]), 
                        fontsize=6,
                        color='white', 
                        bbox=bbox_style,
                        horizontalalignment='center',
                        verticalalignment='bottom',
                        xytext=(0,10),
                        textcoords='offset points',
                        alpha=0.9,
                        zorder=10)

    plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_outliers = os.path.join(jaws_finder_endpoint, 'pca_dbscan_outliers.png')
    plt.savefig(save_outliers, dpi=300)

    # Plot the Size and Port scatter using Plotille
    outlier_plotille = plotille.Figure()
    outlier_plotille.color_mode = 'byte'
    outlier_plotille.width = 80
    outlier_plotille.height = 20
    clustered_indices_pc1 = principal_components[clustered_indices, 0]
    clustered_indices_pc2 = principal_components[clustered_indices, 1]
    outlier_indices_pc1 = principal_components[outlier_indices, 0]
    outlier_indices_pc2 = principal_components[outlier_indices, 1]
    outlier_plotille.scatter(clustered_indices_pc1, clustered_indices_pc2, marker="^")
    outlier_plotille.scatter(outlier_indices_pc1, outlier_indices_pc2, marker="o")
    print(outlier_plotille.show(legend=False))
    
    outlier_data = [
        {
            'org': item['org'],
            'location': item['location'],
            'src_ip': item['src_ip'],
            'src_port': item['src_port'],
            'dst_ip': item['dst_ip'],
            'dst_port': item['dst_port'],
            'size': item['size'],
            'protocol': item['protocol']
        } for i, item in enumerate(data) if clusters[i] == -1
    ]

    print(f"\nFound {len(outlier_data)} outliers:", "\n")
    for item in outlier_data:
        outlier_list = f"{item.get('org')}\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} -> {item.get('dst_ip')}:{item.get('dst_port')}\n{item.get('size')} ({item.get('protocol')})"
        print(outlier_list, "\n")

    update_neo4j(outlier_data, driver, args.database)

    plt.show()


if __name__ == "__main__":
    main()