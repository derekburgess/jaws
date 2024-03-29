from neo4j import GraphDatabase
import pandas as pd
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import random
import time

uri = "bolt://localhost:7687"  # Typical/local Neo4j URI - Updated as needed
username = "neo4j"  # Typical/local Neo4j username - Updated as needed
password = "testtest"  # Typical/l Neo4j password - Updated as needed
driver = GraphDatabase.driver(uri, auth=(username, password)) # Set up the driver

def fetch_data(driver):
    print("\nFetching data from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE p.embedding IS NOT NULL AND p.embedding <> "token_string_too_large"
    OPTIONAL MATCH (src)-[:OWNERSHIP]->(org:Organization)
    OPTIONAL MATCH (dst)-[:OWNERSHIP]->(dst_org:Organization)
    RETURN src.address AS src_ip, 
        dst.address AS dst_ip,
        p.src_port AS src_port, 
        p.dst_port AS dst_port, 
        p.size AS size,
        p.dns_domain AS dns,
        org.name AS org,
        org.hostname AS hostname,
        org.location AS location,
        p.embedding AS embedding
    """
    with driver.session(database="ethcaptures") as session: # Update database="" to your database name
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
                'size': record['size'],
                'dns': record['dns'],
                'org': record['org'],
                'hostname': record['hostname'],
                'location': record['location'],
            })
        return embeddings, data

start_time = time.time()
embeddings, data = fetch_data(driver)
num_embeddings = len(embeddings)
print(f"\nPerforming PCA on {num_embeddings} embeddings...")
embeddings_scaled = StandardScaler().fit_transform(embeddings)
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

print("Using Kneed to recommend EPS...")
kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='convex', direction='increasing')
eps_value = sorted_k_distances[kneedle.knee]
eps_value = float(eps_value)
user_input = input(f"Knee(d) Point: {eps_value}. Press enter to accept, or enter a specific EPS value: ")
if user_input:
    try:
        eps_value = float(user_input)
    except ValueError:
        print("Invalid input. Using the original EPS value...")
        
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(embeddings_scaled)
end_time = time.time()
elapsed_time = end_time - start_time

print("Plotting results...")
fig2 = plt.figure(num=f'PCA/DBSCAN | {int(num_embeddings)} Embeddings(StarCoder) | n_components/samples: 2, eps: {eps_value} | Time: {int(elapsed_time)} seconds', figsize=(12, 10))
fig2.canvas.manager.window.wm_geometry("+50+50")
clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='winter', alpha=0.2, edgecolors='none', marker='o', s=300, zorder=2)

outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.8, marker='^', s=100, label='Outliers', zorder=3)

position_options = {
    'top': {'offset': (0, 10), 'horizontalalignment': 'center', 'verticalalignment': 'bottom'},
    'bottom': {'offset': (0, -10), 'horizontalalignment': 'center', 'verticalalignment': 'top'},
    'right': {'offset': (10, 0), 'horizontalalignment': 'left', 'verticalalignment': 'center'},
    'left': {'offset': (-10, 0), 'horizontalalignment': 'right', 'verticalalignment': 'center'}
}

for i, item in enumerate(data):
    if clusters[i] != -1:
        annotation_text = f"{item.get('org')}\n{item.get('hostname')}({item.get('dns')})\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} > {item.get('dst_ip')}:{item.get('dst_port')}\nSize: {item.get('size')}"

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
    else:
        annotation_text = f"{item.get('org')}\n{item.get('hostname')}({item.get('dns')})\n{item.get('location')}\n{item.get('src_ip')}:{item.get('src_port')} > {item.get('dst_ip')}:{item.get('dst_port')}\nSize: {item.get('size')}"
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