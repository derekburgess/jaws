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


def main():
    parser = argparse.ArgumentParser(description="Perform DBSCAN clustering on embeddings fetched from Neo4j.")
    parser.add_argument("--type", choices=["packet", "org"], default="packet",
                        help="Specify the packet string type to pass (default: packet)")
    parser.add_argument("--database", default="captures", 
                        help="Specify the Neo4j database to connect to (default: captures)")
    
    args = parser.parse_args()
    uri = os.getenv("LOCAL_NEO4J_URI")
    username = os.getenv("LOCAL_NEO4J_USERNAME")
    password = os.getenv("LOCAL_NEO4J_PASSWORD")
    driver = GraphDatabase.driver(uri, auth=(username, password))
    embeddings, data = fetch_data(driver, args.database, args.type)

    # Plot the size of packets over source ports
    fig = plt.figure(num='Packet Size over SRC/DST Port', figsize=(6, 4))

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

    print(f"\nPerforming PCA on Embeddings")
    embeddings_scaled = StandardScaler().fit_transform(embeddings)
    pca = PCA(n_components=2)
    principal_components = pca.fit_transform(embeddings_scaled)

    print("Measuring K-Distance")
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
    fig1 = plt.figure(num='Sorted K-Distance', figsize=(6, 2))
    plt.plot(sorted_k_distances, color='seagreen', marker='o', linestyle='-', linewidth=0.5, alpha=0.8)
    plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    plt.xlabel('INDEX', fontsize=8, color='#666666')
    plt.ylabel('K-DISTANCE', fontsize=8, color='#666666')
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()

    print("Using Kneed to recommend EPS")
    kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
    eps_value = sorted_k_distances[kneedle.knee]
    eps_value = float(eps_value)
    user_input = input(f"Recommended EPS: {eps_value} | Press ENTER to accept, or provide a value: ")
    if user_input:
        try:
            eps_value = float(user_input)
        except ValueError:
            print("Invalid input. Using the recommended EPS value...")

    dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
    clusters = dbscan.fit_predict(principal_components)

    print("\nList of ports and packet sizes:")
    

    print("\nList of outliers:")
    for i, item in enumerate(data):
        if clusters[i] == -1:
            annotation_text = f"{item.get('org')}\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} -> {item.get('dst_ip')}:{item.get('dst_port')}\n{item.get('size')} ({item.get('protocol')})"
            print(annotation_text, "\n")

    # Plot the PCA/DBSCAN Outliers
    fig2 = plt.figure(num=f'PCA/DBSCAN Outliers from Embeddings | n_components/samples: 2, eps: {eps_value}', figsize=(8, 7))
    clustered_indices = clusters != -1
    non_outlier_scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                        c=clusters[clustered_indices], cmap='winter', edgecolors='none', marker='o', s=50, alpha=0.1, zorder=2)

    outlier_indices = clusters == -1
    outlier_scatter = plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
                color='red', marker='^', s=50, label='Outliers', alpha=0.8, zorder=10)

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
    plt.xlabel('PC-1', fontsize=8, color='#666666')
    plt.ylabel('PC-2', fontsize=8, color='#666666')
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()

    plt.show()


if __name__ == "__main__":
    main()