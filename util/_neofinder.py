import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from neo4j import GraphDatabase

uri = "bolt://localhost:7687"  # Update as needed...
username = "neo4j"  # Your Neo4j username
password = "testtest"  # Your Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))

def fetch_data():
    query = """
    MATCH (src:IP)-[p:PACKET]->(dst:IP)
    RETURN p.size AS size, p.label AS label, src.address AS src_ip, dst.address AS dst_ip, p.src_port AS src_port, p.dst_port AS dst_port, p.src_mac AS src_mac, p.dst_mac AS dst_mac
    """
    with driver.session() as session:
        result = session.run(query)
        return pd.DataFrame([record.data() for record in result])

df = fetch_data()
cmap = plt.get_cmap('ocean')
fig = plt.figure(num='finder', figsize=(12, 12))
fig.canvas.manager.window.wm_geometry("+50+50")
color = cmap(0)
subset = df[df['label'] == 'BASE']
plt.scatter(subset['size'], subset['dst_port'], color=[color], label='BASE', alpha=0.2, zorder=10, s=25, marker='^')
subset = df[df['label'] == 'CHUM']
plt.scatter(subset['size'], subset['dst_port'], color='red', label='CHUM', alpha=0.6, zorder=15, s=25, marker='v')
x_ticks = np.linspace(df['size'].min(), df['size'].max(), num=20)
plt.yticks(df['dst_port'].unique(), fontsize=6, zorder=0)
plt.xticks(x_ticks, fontsize=6, zorder=0)
plt.grid(True, linewidth=0.5, color='#BEBEBE', alpha=0.5, zorder=0)
plt.xlabel('Size', fontsize=8)
plt.ylabel('Port', fontsize=8)
plt.title('The kelp forest through the trees...', fontsize=8)
plt.tight_layout()
plt.show()
driver.close()
