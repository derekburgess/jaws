import pandas as pd
import networkx as nx
import matplotlib.pyplot as plt
from scipy.stats import zscore

def compute_out_degree(df, port):
    G = nx.DiGraph()
    filtered_df = df[(df['src_port'] == port) | (df['dst_port'] == port)]

    for _, row in filtered_df.iterrows():
        G.add_edge(row['src_mac'], row['dst_mac'], weight=row['size'])

    weighted_out_degree = G.out_degree(weight='weight')
    node_out, degree_out = max(weighted_out_degree, key=lambda x: x[1], default=(None, None))
    return node_out, degree_out

def compute_in_degree(df, port):
    G = nx.DiGraph()
    filtered_df = df[(df['src_port'] == port) | (df['dst_port'] == port)]

    for _, row in filtered_df.iterrows():
        G.add_edge(row['src_mac'], row['dst_mac'], weight=row['size'])

    weighted_in_degree = G.in_degree(weight='weight')
    in_degree_values = [degree for _, degree in weighted_in_degree]
    z_scores = zscore(in_degree_values)
    significant_nodes = [(node, z) for (node, _), z in zip(weighted_in_degree, z_scores)]
    
    if significant_nodes:
        node_in, z_in_degree = max(significant_nodes, key=lambda x: x[1], default=(None, None))
    else:
        node_in, z_in_degree = None, None

    return node_in, z_in_degree

def analyze_network_data(file_path, output_file, port):
    df = pd.read_csv(file_path)
    unique_ports = set(df['src_port']).union(set(df['dst_port']))
    results = []

    for p in unique_ports:
        node_out, degree_out = compute_out_degree(df, p)
        node_in, z_in_degree = compute_in_degree(df, p)

        results.append({
            'port': p, 
            'dst_mac_out': node_out, 
            'weighted_outdegree': degree_out, 
            'dst_mac_zin': node_in, 
            'z_indegree': z_in_degree
        })

    results_df = pd.DataFrame(results)
    results_df.sort_values(by='z_indegree', ascending=False, inplace=True)
    results_df.to_csv(output_file, index=False)

    visualize_network_graph(file_path, port)

def visualize_network_graph(file_path, port):
    df = pd.read_csv(file_path)
    filtered_df = df[(df['src_port'] == port) | (df['dst_port'] == port)]
    G = nx.DiGraph()

    for _, row in filtered_df.iterrows():
        G.add_edge(row['src_mac'], row['dst_mac'], weight=row['size'])

    weighted_in_degree = G.in_degree(weight='weight')
    in_degree_values = [degree for _, degree in weighted_in_degree]
    z_scores = zscore(in_degree_values)

    node_color = []
    node_shape = []
    for node, degree in weighted_in_degree:
        z = z_scores[list(G.nodes).index(node)]
        node_color.append('gray' if z <= 1.645 else 'blue')  # Blue for normal, red for high z-score
        node_shape.append('o' if z <= 1.645 else 'D')  # Circle for normal, diamond for high z-score

    fig = plt.figure(num='sub', figsize=(8, 8))
    fig.canvas.manager.window.wm_geometry("+50+50")

    pos = nx.spring_layout(G, k=0.5, iterations=50, seed=150)

    for shape in set(node_shape):
        nodes_of_current_shape = [s for s, shape_ in zip(G.nodes, node_shape) if shape_ == shape]
        colors_of_current_shape = [c for c, shape_ in zip(node_color, node_shape) if shape_ == shape]
        nx.draw_networkx_nodes(G, pos, node_size=100, 
                            nodelist=nodes_of_current_shape, 
                            node_color=colors_of_current_shape, node_shape=shape)
    
    z_scores_dict = {node: z for node, z in zip(G.nodes, z_scores)}
    ip_dict = {row['src_mac']: row['src_ip'] for _, row in filtered_df.iterrows()}
    ip_dict.update({row['dst_mac']: row['dst_ip'] for _, row in filtered_df.iterrows()})
    labels = {node: f'{node}\n{ip_dict.get(node, "N/A")}\n{z_scores_dict[node]:.2f}' for node in G.nodes()}
    nx.draw_networkx_edges(G, pos, width=0.5, edge_color='#BEBEBE')
    label_pos = {node: (pos[node][0], pos[node][1]+0.075) for node in G.nodes()}
    nx.draw_networkx_labels(G, label_pos, labels=labels, font_size=8, font_color='black', bbox=dict(facecolor='white', alpha=0.5, edgecolor='none', boxstyle='round,pad=0.25'))
    #edge_labels = {(row['src_mac'], row['dst_mac']): str(port) for _, row in filtered_df.iterrows()}
    #nx.draw_networkx_edge_labels(G, pos, edge_labels=edge_labels, font_size=6)
    plt.title(f'Graph Port: {port}', fontsize=8)
    plt.tight_layout()
    plt.show()

file_path = './data/sets/packets_.csv'
output_file = './data/subgraph.csv'
port = int(input("Enter a port to map: "))
analyze_network_data(file_path, output_file, port)
