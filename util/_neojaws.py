from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
import matplotlib.pyplot as plt
import requests

uri = "bolt://localhost:7687"  # Update as needed...
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))
api_key = 'KEY'  # Replace with your ipinfo API key

def get_general_ip_info(ip_address, api_key):
    url = f"https://ipinfo.io/{ip_address}/json"
    headers = {
        'Authorization': f'Bearer {api_key}'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return None

def fetch_embeddings_and_src_ip(api_key):
    query = """
    MATCH (src:IP)
    WHERE src.embedding IS NOT NULL
    RETURN src.address AS src_ip, src.embedding AS embedding
    """
    with driver.session() as session:
        result = session.run(query)
        src_ips = []
        embeddings = []
        orgs = []
        hostnames = []
        locations = []
        for record in result:
            embedding = np.array(record['embedding'])
            embeddings.append(embedding)
            src_ip = record['src_ip']
            src_ips.append(src_ip)
            ip_info = get_general_ip_info(src_ip, api_key)
            org = ip_info.get('org', 'N/A') if ip_info else 'N/A'
            hostname = ip_info.get('hostname', 'N/A') if ip_info else 'N/A'
            loc = ip_info.get('loc', 'N/A') if ip_info else 'N/A'
            orgs.append(org)
            hostnames.append(hostname)
            locations.append(loc)
        return np.array(embeddings), src_ips, orgs, hostnames, locations
    
def update_clusters_in_neo4j(src_ips, clusters, driver):
    update_query = """
    UNWIND $data AS row
    MATCH (src:IP {address: row.ip})
    SET src.cluster = row.cluster
    """
    data = [{'ip': ip, 'cluster': int(cluster)} for ip, cluster in zip(src_ips, clusters)]
    with driver.session() as session:
        session.run(update_query, {'data': data})

print("Fetching embeddings and performing PCA...")
embeddings, src_ips, orgs, hostnames, locations = fetch_embeddings_and_src_ip(api_key)
scaler = StandardScaler()
embeddings_scaled = scaler.fit_transform(embeddings)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(embeddings_scaled)

print("Clustering...")
dbscan = DBSCAN(eps=50, min_samples=2)
clusters = dbscan.fit_predict(embeddings_scaled)
update_clusters_in_neo4j(src_ips, clusters, driver)

print("Plotting...")
fig = plt.figure(num='finder', figsize=(12, 12))
fig.canvas.manager.window.wm_geometry("+50+50")

clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='ocean', alpha=0.8, edgecolors='none', marker='^', s=100)

outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.6, marker='o', s=100, label='Outliers')

for i, txt in enumerate(src_ips):
    annotation_text = f"{txt}\n{hostnames[i]}\n{orgs[i]}\n{locations[i]}"
    plt.annotate(annotation_text, 
                 (principal_components[i, 0], principal_components[i, 1]), 
                 fontsize=6, 
                 bbox=dict(boxstyle="round,pad=0.2", facecolor='#BEBEBE', edgecolor='none', alpha=0.3),
                 horizontalalignment='left', 
                 verticalalignment='bottom',
                 xytext=(-10,10),
                 textcoords='offset points')

plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.show()
