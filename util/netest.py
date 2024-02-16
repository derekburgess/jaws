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
    WITH src, dst, p
    OPTIONAL MATCH (src)-[:OWNERSHIP]->(org:Organization)
    OPTIONAL MATCH (dst)-[:OWNERSHIP]->(dst_org:Organization)
    RETURN src.address AS src_ip, 
        dst.address AS dst_ip,
        p.src_port AS src_port, 
        p.dst_port AS dst_port, 
        p.src_mac AS src_mac, 
        p.dst_mac AS dst_mac,  
        p.protocol AS protocol,
        p.tcp_flags AS tcp,
        p.size AS size, 
        p.payload AS payload, 
        p.payload_ascii AS ascii,
        p.http_url AS http, 
        p.dns_domain AS dns,
        org.name AS org,
        org.hostname AS hostname,
        org.location AS location 
    """
    with driver.session() as session:
        result = session.run(query)
        data = [record.data() for record in result]
        return pd.DataFrame(data)

start_time = time.time()
print("\nFetching data and performing one-hot encoding and PCA...")
#sample_fraction = 0.2 #Sample non-embedding data!
df = fetch_data()
#df = df.sample(frac=sample_fraction)
df = pd.get_dummies(df, columns=['src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'protocol', 'tcp', 'size', 'payload', 'ascii', 'http', 'dns', 'org', 'hostname', 'location'], drop_first=True)
total_records = len(df)
scaler = StandardScaler()
data_scaled = scaler.fit_transform(df)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(data_scaled)

print("Using Kneed to recommend EPS...")
min_samples = 2
nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
nearest_neighbors.fit(data_scaled)
distances, _ = nearest_neighbors.kneighbors(data_scaled)
kneedle = KneeLocator(range(len(distances)), distances[:, 1], curve='convex', direction='increasing')
eps_value = distances[kneedle.knee, 1]
eps_value = float(eps_value)
user_input = input(f"Knee Point/EPS value is {eps_value}. Press enter to accept, or enter a specific value: ")
if user_input:
    try:
        eps_value = float(user_input)
    except ValueError:
        print("Invalid input. Using the original EPS value...")

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
