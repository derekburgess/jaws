from neo4j import GraphDatabase
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from sklearn.neighbors import NearestNeighbors
from kneed import KneeLocator
import matplotlib.pyplot as plt
import time


# Test script for packet embeddings. Fetches data from Neo4j, performs PCA, and then applies DBSCAN for clustering. Does not plot labels. Intended for testing and public demonstrations.


uri = "bolt://localhost:7687"  # Typical/local Neo4j URI - Updated as needed
username = "neo4j"  # Typical/local Neo4j username - Updated as needed
password = "testtest"  # Typical/l Neo4j password - Updated as needed
driver = GraphDatabase.driver(uri, auth=(username, password))


def fetch_edge_embeddings(driver):
    print("\nFetching edge embeddings from Neo4j...")
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    WHERE p.embedding IS NOT NULL AND p.embedding <> "token_string_too_large"
    RETURN p.embedding AS embedding
    """
    with driver.session(database="ethcaptures") as session:
        result = session.run(query)
        embeddings = []
        for record in result:
            embedding = np.array(record['embedding'])
            embeddings.append(embedding)
        return np.array(embeddings)

start_time = time.time()
embeddings = fetch_edge_embeddings(driver)
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
fig2 = plt.figure(num=f'PCA/DBSCAN | Embeddings(AI)', figsize=(12, 10))
fig2.canvas.manager.window.wm_geometry("+50+50")
clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='winter', alpha=0.2, edgecolors='none', marker='o', s=200, zorder=2)


outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.8, marker='^', s=200, label='Outliers', zorder=3)

plt.title(f'PCA/DBSCAN | {int(num_embeddings)} Embeddings(AI) | n_components/samples: 2, eps: {eps_value} | Time: {int(elapsed_time)} seconds', size=8)
plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.show()
