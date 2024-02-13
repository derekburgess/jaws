from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
import matplotlib.pyplot as plt
import requests

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))
api_key = 'KEY'  # Replace with your ipinfo API key

def get_general_ip_info(ip_address, api_key):
    url = f"https://ipinfo.io/{ip_address}/json"
    headers = {'Authorization': f'Bearer {api_key}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return None

def fetch_embeddings_and_src_ip(api_key):
    query = """
    MATCH (src:IP)
    WHERE src.embedding IS NOT NULL
    RETURN src.address AS src_ip, src.embedding AS embedding, src.info AS info
    """
    with driver.session() as session:
        result = session.run(query)
        src_ips, embeddings, orgs, hostnames, locations, infos = [], [], [], [], [], []
        for record in result:
            embedding = np.array(record['embedding'])
            embeddings.append(embedding)
            src_ip = record['src_ip']
            src_ips.append(src_ip)
            ip_info = get_general_ip_info(src_ip, api_key)
            if ip_info:
                orgs.append(ip_info.get('org', 'N/A'))
                hostnames.append(ip_info.get('hostname', 'N/A'))
                locations.append(ip_info.get('loc', 'N/A'))
            else:
                orgs.append('N/A')
                hostnames.append('N/A')
                locations.append('N/A')
            infos.append(record['info'])
        return np.array(embeddings), src_ips, orgs, hostnames, locations, infos

def update_clusters_in_neo4j(src_ips, clusters, driver):
    update_query = """
    UNWIND $data AS row
    MATCH (src:IP {address: row.ip})
    SET src.cluster = row.cluster
    """
    data = [{'ip': ip, 'cluster': int(cluster)} for ip, cluster in zip(src_ips, clusters)]
    with driver.session() as session:
        session.run(update_query, {'data': data})

print("\nFetching embeddings and performing PCA...")
embeddings, src_ips, orgs, hostnames, locations, infos = fetch_embeddings_and_src_ip(api_key)
scaler = StandardScaler()
embeddings_scaled = scaler.fit_transform(embeddings)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(embeddings_scaled)

print("Measuring k-distance...")
min_samples = 2
nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
nearest_neighbors.fit(embeddings_scaled)
distances, indices = nearest_neighbors.kneighbors(embeddings_scaled)
k_distances = distances[:, min_samples - 1]
sorted_k_distances = np.sort(k_distances)

fig1 = plt.figure(num='k-distance', figsize=(12, 4))
fig1.canvas.manager.window.wm_geometry("+1300+50")
plt.plot(sorted_k_distances, marker='s', color='blue', linestyle='-', linewidth=0.5)
plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.savefig("./data/k-distance_plot.png")

eps_value = float(input("Enter an EPS value for DBSCAN based on the k-distance plot: "))
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(embeddings_scaled)
update_clusters_in_neo4j(src_ips, clusters, driver)

print("Plotting results...")
unique_clusters = set(clusters) - {-1}
n_clusters = len(unique_clusters)
outliers_present = -1 in clusters

fig, axs = plt.subplots(1, n_clusters + outliers_present, figsize=(6 * (n_clusters + outliers_present), 6))
fig.canvas.manager.window.wm_geometry("+50+50")

for idx, cluster in enumerate(unique_clusters):
    cluster_indices = clusters == cluster
    axs[idx].scatter(principal_components[cluster_indices, 0], principal_components[cluster_indices, 1], 
                     cmap='winter', alpha=0.6, edgecolors='none', marker='^', s=100)
    
    for i in cluster_indices.nonzero()[0]:
        annotation_text = f"{src_ips[i]}\n{hostnames[i]}\n{orgs[i]}\n{locations[i]}"
        bbox_style = dict(boxstyle="round,pad=0.4", facecolor='#BEBEBE', edgecolor='none', alpha=0.1)
        axs[idx].annotate(annotation_text, 
                          (principal_components[i, 0], principal_components[i, 1]), 
                          fontsize=6,
                          color='#333333',
                          bbox=bbox_style,
                          horizontalalignment='right',
                          verticalalignment='top',
                          xytext=(0,-10),
                          textcoords='offset points')
    
    axs[idx].grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    axs[idx].tick_params(axis='x', which='both', bottom=False, labelbottom=False)
    axs[idx].tick_params(axis='y', which='both', left=False, labelleft=False)

if outliers_present:
    outlier_indices = clusters == -1
    axs[-1].scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
                    color='red', alpha=0.8, marker='o', s=100, label='Outliers')
    
    for i in outlier_indices.nonzero()[0]:
        annotation_text = f"{src_ips[i]}\n{hostnames[i]}\n{orgs[i]}\n{locations[i]}"
        bbox_style = dict(boxstyle="round,pad=0.4", facecolor='#333333', edgecolor='none', alpha=0.8)
        axs[-1].annotate(annotation_text, 
                         (principal_components[i, 0], principal_components[i, 1]), 
                         fontsize=8,
                         color='white', 
                         bbox=bbox_style,
                         horizontalalignment='right',
                         verticalalignment='bottom',
                         xytext=(0,10),
                         textcoords='offset points')

    axs[-1].grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
    axs[-1].tick_params(axis='x', which='both', bottom=False, labelbottom=False)
    axs[-1].tick_params(axis='y', which='both', left=False, labelleft=False)

plt.tight_layout()
plt.show()

