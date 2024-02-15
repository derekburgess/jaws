from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import requests
import random
import time

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))
api_key = 'KEY'  # Replace with your IPinfo key

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

start_time = time.time()
print("\nFetching embeddings and performing PCA...")
embeddings, src_ips, orgs, hostnames, locations, infos = fetch_embeddings_and_src_ip(api_key)
num_embeddings = len(embeddings)
scaler = StandardScaler()
embeddings_scaled = scaler.fit_transform(embeddings)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(embeddings_scaled)

#See the commented section below- I have opted to try Kneed as a way to determine the optimal EPS value for DBSCAN clustering. Prior to that I was using the k-distance nearest neighbors approach and returning a plot for visual inspection and selection. If you would like that approach these sections are fairly interchangeable.

print("Using Kneed to recommend EPS...")
min_samples = 2
sorted_k_distances = pca.explained_variance_ratio_
kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
eps_value = sorted_k_distances[kneedle.knee]
eps_value = float(eps_value)
user_input = input(f"Knee Point/EPS value is {eps_value}. Press enter to accept, or enter a specific value: ")
if user_input:
    try:
        eps_value = float(user_input)
    except ValueError:
        print("Invalid input. Using the original EPS value...")
        
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(embeddings_scaled)
update_clusters_in_neo4j(src_ips, clusters, driver)
end_time = time.time()
elapsed_time = end_time - start_time

"""
Using k-distance nearest neighbors to determine the optimal EPS value for DBSCAN clustering. Returns a plot and expect user-input. Switch out with the Kneed approach above.

start_time = time.time()
print("\nFetching embeddings and performing PCA...")
embeddings, src_ips, orgs, hostnames, locations, infos = fetch_embeddings_and_src_ip(api_key)
num_embeddings = len(embeddings)
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
plt.savefig("./data/k-distance.png")

eps_value = float(input("Enter an EPS value: "))
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(embeddings_scaled)
update_clusters_in_neo4j(src_ips, clusters, driver)
end_time = time.time()
elapsed_time = end_time - start_time
"""

print("Plotting results...")
fig2 = plt.figure(num=f'PCA/DBSCAN | {int(num_embeddings)} Embeddings(StarCoder) | n_components/samples: 2, eps: {eps_value} | Time: {int(elapsed_time)} seconds', figsize=(12, 10))
fig2.canvas.manager.window.wm_geometry("+50+50")
clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='winter', alpha=0.2, edgecolors='none', marker='o', s=300, zorder=2)

outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.8, marker='^', s=100, label='Outliers', zorder=3)

# Could maybe try another approach here, but helps reduce overlapping...

position_options = {
    'top': {'offset': (0, 10), 'horizontalalignment': 'center', 'verticalalignment': 'bottom'},
    'bottom': {'offset': (0, -10), 'horizontalalignment': 'center', 'verticalalignment': 'top'},
    'right': {'offset': (10, 0), 'horizontalalignment': 'left', 'verticalalignment': 'center'},
    'left': {'offset': (-10, 0), 'horizontalalignment': 'right', 'verticalalignment': 'center'}
}

for i, txt in enumerate(src_ips):
    if clusters[i] != -1:
        annotation_text = f"{txt}\n{hostnames[i]}\n{orgs[i]}\n{locations[i]}"
        bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#BEBEBE', edgecolor='none', alpha=0.05)

        label_position_key = random.choice(list(position_options.keys()))
        label_position = position_options[label_position_key]
        
        plt.annotate(annotation_text, 
                     (principal_components[i, 0], principal_components[i, 1]), 
                     fontsize=6,
                     color='#666666',
                     bbox=bbox_style,
                     horizontalalignment=label_position['horizontalalignment'],
                     verticalalignment=label_position['verticalalignment'],
                     xytext=label_position['offset'],
                     textcoords='offset points',
                     zorder=1)
            
for i, txt in enumerate(src_ips):
    if clusters[i] == -1:
        annotation_text = f"{txt}\n{hostnames[i]}\n{orgs[i]}\n{locations[i]}"
        bbox_style = dict(boxstyle="round,pad=0.2", facecolor='#333333', edgecolor='none', alpha=0.8)
        plt.annotate(annotation_text, 
                     (principal_components[i, 0], principal_components[i, 1]), 
                     fontsize=6,
                     color='white', 
                     bbox=bbox_style,
                     horizontalalignment='center',
                     verticalalignment='bottom',
                     xytext=(0,10),
                     textcoords='offset points',
                     zorder=1)

plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.show()

