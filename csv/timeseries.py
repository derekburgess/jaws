import pandas as pd
import matplotlib.pyplot as plt

def filter_and_plot(src_ip, csv_files):
    for csv_file in csv_files:
        try:
            df = pd.read_csv(csv_file)
        except (FileNotFoundError, pd.errors.EmptyDataError):
            print(f"Failed to load {csv_file} or the file is empty...\n")
            continue

        print("\n", df[df['src_ip'] == src_ip], "\n")
        filtered_df = df[df['src_ip'] == src_ip]

        if filtered_df.empty:
            print(f"No packets found for: {src_ip} in {csv_file}", "\n")
            continue

        plt.figure(num='time', figsize=(20, 4))
        filtered_df.loc[:, 'timestamp'] = pd.to_datetime(filtered_df['timestamp'], unit='ms')
        plt.plot_date(filtered_df['timestamp'], filtered_df['size'], linestyle='solid')
        plt.title(f"Packet Size over Time for: {src_ip} in {csv_file}", fontsize=8)
        plt.xlabel('Time', fontsize=8)
        plt.ylabel('Packet Size (bytes)', fontsize=8)
        plt.grid(True, linewidth=0.25, color='#BEBEBE', linestyle='-')
        plt.tick_params(axis='both', which='major', labelsize=8)
        plt.tight_layout()
        plt.show()

if __name__ == "__main__":
    src_ip_to_filter = input("Enter an IP address to filter: ")
    csv_files = ['./data/sets/packets_.csv']
    filter_and_plot(src_ip_to_filter, csv_files)
