from neo4j import GraphDatabase
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from kneed import KneeLocator
import matplotlib.pyplot as plt
import time

uri = "bolt://localhost:7687"  # Update as needed
username = "neo4j"  # Local Neo4j username
password = "testtest"  # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_embeddings(driver):
    query = """
    MATCH (src:IP)
    WHERE src.embedding IS NOT NULL
    RETURN src.address AS src_ip, src.embedding AS embedding
    """
    with driver.session() as session:
        result = session.run(query)
        src_ips, embeddings= [], []
        for record in result:
            embedding = np.array(record['embedding'])
            embeddings.append(embedding)
            src_ips.append(record['src_ip'])
        return np.array(embeddings), src_ips

start_time = time.time()
print("\nFetching embeddings and performing PCA...")
embeddings, src_ips = fetch_embeddings(driver)
num_embeddings = len(embeddings)
scaler = StandardScaler()
embeddings_scaled = scaler.fit_transform(embeddings)
pca = PCA(n_components=2)
principal_components = pca.fit_transform(embeddings_scaled)

print("Using Kneed to set EPS...")
min_samples = 2
sorted_k_distances = pca.explained_variance_ratio_
kneedle = KneeLocator(range(len(sorted_k_distances)), sorted_k_distances, curve='concave', direction='increasing')
eps_value = sorted_k_distances[kneedle.knee]
eps_value = float(eps_value)
user_input = input(f"Knee Point/EPS value is {eps_value}. Press enter to accept, or type a new value: ")
if user_input:
    try:
        eps_value = float(user_input)
    except ValueError:
        print("Invalid input. Using the original EPS value.")
        
dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
clusters = dbscan.fit_predict(embeddings_scaled)
end_time = time.time()
elapsed_time = end_time - start_time

print("Plotting results...")
fig2 = plt.figure(num=f'PCA/DBSCAN | Embeddings(OpenAI)', figsize=(12, 10))
fig2.canvas.manager.window.wm_geometry("+50+50")
clustered_indices = clusters != -1
scatter = plt.scatter(principal_components[clustered_indices, 0], principal_components[clustered_indices, 1], 
                      c=clusters[clustered_indices], cmap='winter', alpha=0.2, edgecolors='none', marker='o', s=200, zorder=2)

outlier_indices = clusters == -1
plt.scatter(principal_components[outlier_indices, 0], principal_components[outlier_indices, 1], 
            color='red', alpha=0.8, marker='^', s=200, label='Outliers', zorder=3)

plt.title(f'PCA/DBSCAN | {int(num_embeddings)} Embeddings(OpenAI) | n_components/samples: 2, eps: {eps_value} | Time: {int(elapsed_time)} seconds', size=8)
plt.grid(color='#BEBEBE', linestyle='-', linewidth=0.25, alpha=0.5)
plt.xticks(fontsize=8)
plt.yticks(fontsize=8)
plt.tight_layout()
plt.show()
