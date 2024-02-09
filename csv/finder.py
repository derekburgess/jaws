import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.cm as cm

csv_files = ['./data/sets/packets_.csv']
cmap = plt.get_cmap('ocean')

for i, csv_file in enumerate(csv_files):
    df = pd.read_csv(csv_file)
    grouped = df.groupby(['dst_port', 'src_ip', 'src_mac', 'dst_ip', 'dst_mac'])['size'].sum().reset_index()
    grouped.sort_values(['dst_port', 'size'], ascending=[True, False], inplace=True)
    grouped.drop_duplicates(subset='dst_port', keep='first', inplace=True)
    grouped.columns = ['port', 'src_ip', 'src_mac', 'dst_ip', 'dst_mac', 'total_size']
    grouped.sort_values('total_size', ascending=False, inplace=True)
    grouped.to_csv('./data/finder.csv', index=False)
    print('\n', grouped.head(50), '\n')

fig = plt.figure(num='finder', figsize=(12, 12))
fig.canvas.manager.window.wm_geometry("+50+50")

for i, csv_file in enumerate(csv_files):
    df = pd.read_csv(csv_file)
    subset = df[df['label'] == 'BASE']
    color = cmap(i / len(csv_files))
    plt.scatter(subset['size'], subset['dst_port'], color=[color], label=f'BASE from {csv_file}', alpha=0.2, zorder=10, s=25, marker='^')
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