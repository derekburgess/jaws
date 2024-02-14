from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import time

# Test script for non-embedding, raw packet data. Fetches data from Neo4j, performs one-hot encoding and PCA, and then applies DBSCAN for clustering. Does not plot labels. Intended for testing and public demonstrations.

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data():
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    RETURN p.protocol AS protocol, p.tcp_flags AS tcp_flags, src.address AS src_ip, dst.address AS dst_ip, p.src_port AS src_port, p.dst_port AS dst_port, p.src_mac AS src_mac, p.dst_mac AS dst_mac
    """
    with driver.session() as session:
        result = session.run(query)
        data = [record.data() for record in result]
        return pd.DataFrame(data)

start_time = time.time()
print("\nFetching data and performing one-hot encoding and PCA...")
df = fetch_data()
src_ips = df['src_ip'].tolist()
df = pd.get_dummies(df, columns=['protocol', 'tcp_flags', 'src_ip', 'dst_ip', 'src_port', 'dst_port', 'src_mac', 'dst_mac'], drop_first=True)
total_records = len(df)
scaler = StandardScaler()
data_scaled = scaler.fit_transform(df)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(data_scaled)

print("Using Kneed to set EPS...")
min_samples = 2
sorted_k_distances = pca.explained_variance_ratio_
kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
eps_value = sorted_k_distances[kneedle.knee]

print("Knee Point/EPS value:", eps_value)
eps_value = float(eps_value)
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(data_scaled)
end_time = time.time()
elapsed_time = end_time - start_time

print("Plotting results...")
fig2 = plt.figure(num=f'PCA/DBSCAN | Raw Packets', figsize=(12, 10))
fig2.canvas.manager.window.wm_geometry("+50+50")
clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='winter', alpha=0.2, edgecolors='none', marker='o', s=200, zorder=2)

outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.8, marker='^', s=200, label='Outliers', zorder=3)

plt.title(f"PCA/DBSCAN | {int(total_records)} Raw Packets | n_components/samples: 2, eps: {eps_value} | Time: {int(elapsed_time)} seconds", size=8)
plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.show()
