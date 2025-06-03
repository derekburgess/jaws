import os
import argparse
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import plotille
from rich.console import Console
from jaws.jaws_config import *
from jaws.jaws_utils import dbms_connection, render_success_panel, render_error_panel, render_info_panel


def fetch_data_for_dbscan(driver, database):
    query = """
    MATCH (traffic:TRAFFIC)
    RETURN traffic.IP_ADDRESS AS ip_address,
           traffic.PORT AS port,
           traffic.ORGANIZATION AS org,
           traffic.HOSTNAME AS hostname,
           traffic.LOCATION AS location,
           traffic.EMBEDDING AS embedding,
           traffic.TOTAL_SIZE AS total_size
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        embeddings = []
        data = []
        for record in result:
            embeddings.append(np.array(record['embedding']))
            data.append({
                'ip_address': record['ip_address'],
                'port': record['port'],
                'org': record['org'],
                'hostname': record['hostname'],
                'location': record['location'],
                'total_size': record['total_size'],
            })
        return embeddings, data


def fetch_data_for_portsize(driver, database):
    query = """
    MATCH (src_port:PORT)-[p:PACKET]->(dst_port:PORT)
    RETURN p.SIZE AS size, src_port.PORT AS src_port, dst_port.PORT AS dst_port
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        plot_data = [{'size': record['size'], 'src_port': record['src_port'], 'dst_port': record['dst_port']} 
                     for record in result]
    return plot_data


def add_outlier_to_database(outlier_list, driver, database):
    query = """
    UNWIND $outliers AS outlier
    MATCH (traffic:TRAFFIC {IP_ADDRESS: outlier.ip_address, PORT: outlier.port})
    SET traffic.OUTLIER = true
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
    print(portsize_plotille.show(legend=False))


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
    print(kdistance_plotille.show(legend=False))


def main():
    parser = argparse.ArgumentParser(description="Perform DBSCAN clustering on embeddings fetched from the database.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    args = parser.parse_args()
    console = Console()

    driver = dbms_connection(args.database)
    if driver is None:
        return
    
    embeddings, data = fetch_data_for_dbscan(driver, args.database)

    plot_data = fetch_data_for_portsize(driver, args.database)
    message = "The below plot shows the packet size over ports.\nIt is useful for identifying ports that are sending or receiving large amounts of data."
    console.print(render_info_panel("INFORMATION", message, console))
    plot_size_over_ports(plot_data, FINDER_ENDPOINT)

    embeddings_scaled = StandardScaler().fit_transform(embeddings)
    message = f"Performing PCA(Principal Component Analysis) on embeddings({len(embeddings_scaled)}).\nThis reduces the dimensionality of the embeddings to 2 dimensions."
    console.print(render_info_panel("INFORMATION", message, console))
    pca = PCA(n_components=2)
    principal_components = pca.fit_transform(embeddings_scaled)
    
    message = "Measuring K-Distance. This is used to determine the optimal epsilon value\nfor DBSCAN(Density-Based Spatial Clustering of Applications with Noise)."
    console.print(render_info_panel("INFORMATION", message, console))
    min_samples = 2
    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
    nearest_neighbors.fit(principal_components)
    distances, _ = nearest_neighbors.kneighbors(principal_components)
    k_distances = distances[:, min_samples - 1]
    sorted_k_distances = np.sort(k_distances)
    plot_k_distances(sorted_k_distances, FINDER_ENDPOINT)

    message = "Using Kneed to recommend EPS.\nKneed is a library that helps us find the knee point in the K-Distance plot."
    console.print(render_info_panel("INFORMATION", message, console))
    kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
    if kneedle.knee is not None:
        eps_value = sorted_k_distances[kneedle.knee]
        message = f"Knee point found at index: {kneedle.knee}"
        console.print(render_info_panel("INFORMATION", message, console))
    else:
        message = "Knee point not found. Using default EPS."
        console.print(render_info_panel("INFORMATION", message, console))
        eps_value = np.median(sorted_k_distances)

    message = f"Matplotlib plots will be generated after passing an EPS value."
    console.print(render_info_panel("INFORMATION", message, console))
    user_input = input(f"Recommended EPS: {eps_value} | Press ENTER to accept, or provide a value: ")
    if user_input:
        try:
            eps_value = float(user_input)
        except ValueError:
            message = "Invalid input. Using the recommended EPS value."
            console.print(render_error_panel("ERROR", message, console))

    message = f"Passing EPS: {eps_value}"
    console.print(render_info_panel("INFORMATION", message, console))

    dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
    clusters = dbscan.fit_predict(principal_components)

    message = "The below plot shows the PCA/DBSCAN outliers, in red, from the embeddings.\nAdditionally, embedding clusters are shown to help understand how outliers are distributed amongst noise."
    console.print(render_info_panel("INFORMATION", message, console))

    plt.figure(num=f'PCA/DBSCAN Outliers from Embeddings | n_components/samples: 2, eps: {eps_value}', figsize=(8, 7))
    clustered_indices = clusters != -1
    plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                c=clusters[clustered_indices], cmap='winter', edgecolors='none', marker='^', s=50, alpha=0.1, zorder=2)

    outlier_indices = clusters == -1
    plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
                color='red', marker='o', s=50, label='Outliers', alpha=0.8, zorder=10)

    for i, item in enumerate(data):
        annotation_text = f"{item['ip_address']}:{item['port']}({item['total_size']})\n{item['org']}\n{item['hostname']}\n{item['location']}"
        if clusters[i] == -1:
            # Outlier
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
        else:
            # Non-Outlier
            bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#BEBEBE', edgecolor='none', alpha=0.5)
            plt.annotate(annotation_text, 
                        (principal_components[i, 0], principal_components[i, 1]), 
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
    save_outliers = os.path.join(FINDER_ENDPOINT, 'pca_dbscan_outliers.png')
    plt.savefig(save_outliers, dpi=90)

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
            'ip_address': item['ip_address'],
            'port': item['port'],
            'org': item['org'],
            'hostname': item['hostname'],
            'location': item['location'],
            'total_size': item['total_size']
        } for i, item in enumerate(data) if clusters[i] == -1
    ]

    #message = f"\nFound {len(outlier_data)} Outliers:"
    #console.print(render_info_panel("INFORMATION", message, console))
    #for item in outlier_data:
    #    outlier_list = f"IP Address: {item['ip_address']}\nPort: {item['port']}\nOrganization: {item['org']}\nHostname: {item['hostname']}\nLocation: {item['location']}\nTotal Size: {item['total_size']}"
    #    print(outlier_list, "\n")

    add_outlier_to_database(outlier_data, driver, args.database)

    plt.show()
    message = f"Plots saved to: {FINDER_ENDPOINT}"
    console.print(render_success_panel("PROCESS COMPLETE", message, console))

    driver.close()

if __name__ == "__main__":
    main()