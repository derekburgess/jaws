import pyshark
import pandas as pd
import os

batch_size = 100
chum_addr = 'AWS IP ADDR'  # AWS IP address of the chum server
df = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'dns_domain', 'http_url', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'timestamp', 'label'])

if os.path.exists('./data/packets.csv'):
    existing_data = pd.read_csv('./data/packets.csv')
    if not existing_data.empty:
        packet_id = existing_data['packet_id'].max() + 1
    else:
        packet_id = 0
else:
    packet_id = 0

def convert_hex_tcp_flags(hex_flags):
    flags_mapping = {
        '0x1': 'FIN',
        '0x2': 'SYN',
        '0x4': 'RST',
        '0x8': 'PSH',
        '0x10': 'ACK',
        '0x20': 'URG',
        '0x40': 'ECE',
        '0x80': 'CWR'
    }

    readable_flags = []
    binary_flags = format(int(hex_flags, 16), '08b')

    for flag_hex, flag_name in flags_mapping.items():
        if int(binary_flags, 2) & int(flag_hex, 16):
            readable_flags.append(flag_name)
    return ', '.join(readable_flags)

def process_packet(packet):
    global df, packet_id
    packet_info = {
        "packet_id": packet_id,
        "protocol": packet.transport_layer if hasattr(packet, 'transport_layer') else 'UNKNOWN',
        "tcp_flags": convert_hex_tcp_flags(packet.tcp.flags) if 'TCP' in packet else 'NA',
        "dns_domain": packet.dns.qry_name if 'DNS' in packet and hasattr(packet.dns, 'qry_name') else 'NA',
        "http_url": packet.http.request.full_uri if 'HTTP' in packet and hasattr(packet.http, 'request') and hasattr(packet.http.request, 'full_uri') else 'NA',
        "src_ip": '0.0.0.0',
        "src_port": 0,
        "src_mac": packet.eth.src if 'ETH' in packet else 'NA',
        "dst_ip": '0.0.0.0',
        "dst_port": 0,
        "dst_mac": packet.eth.dst if 'ETH' in packet else 'NA',
        "size": len(packet),
        "timestamp": float(packet.sniff_time.timestamp()) * 1000,
        "label": 'BASE'
    }
    if 'IP' in packet:
        packet_info["src_ip"] = packet.ip.src
        packet_info["dst_ip"] = packet.ip.dst
        packet_info["src_port"] = int(packet[packet.transport_layer].srcport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].srcport.isdigit() else 0
        packet_info["dst_port"] = int(packet[packet.transport_layer].dstport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].dstport.isdigit() else 0
        packet_info["label"] = 'CHUM' if packet.ip.dst == chum_addr else 'BASE'

    if packet_info['dst_ip'] == chum_addr:
        print(f"\n>>>> [{packet_info['packet_id']}] [PROTOCOL: {packet_info['protocol']} | {packet_info['tcp_flags']}] [DNS: {packet_info['dns_domain']}] [HTTP: {packet_info['http_url']}] [SRC: {packet_info['src_ip']} | {packet_info['src_port']} | {packet_info['src_mac']}] [DST :{packet_info['dst_ip']} | {packet_info['dst_port']} | {packet_info['dst_mac']}] [SIZE :{packet_info['size']}] [{packet_info['timestamp']}] <<<< {packet_info['label']} PACKET")
    else:
        print(f"\n>>>> [{packet_info['packet_id']}] [PROTOCOL: {packet_info['protocol']} | {packet_info['tcp_flags']}] [DNS: {packet_info['dns_domain']}] [HTTP: {packet_info['http_url']}] [SRC: {packet_info['src_ip']} | {packet_info['src_port']} | {packet_info['src_mac']}] [DST :{packet_info['dst_ip']} | {packet_info['dst_port']} | {packet_info['dst_mac']}] [SIZE :{packet_info['size']}] [{packet_info['timestamp']}] <<<< {packet_info['label']} PACKET")

    new_row = pd.Series(packet_info, name='x')
    df = pd.concat([df, pd.DataFrame(new_row).T], ignore_index=True)

    if len(df) >= batch_size:
        try:
            existing_data = pd.read_csv('./data/packets.csv')
        except (FileNotFoundError, pd.errors.EmptyDataError):
            existing_data = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'dns_domain', 'http_url', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'timestamp', 'label'])

        combined_data = pd.concat([existing_data, df], ignore_index=True)
        combined_data.to_csv('./data/packets.csv', index=False, na_rep='NA')
        print(f"\nSaved {batch_size} packets to CSV file.", "\n")
        df = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'dns_domain', 'http_url', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'timestamp', 'label'])

    packet_id += 1

if __name__ == "__main__":
    print(f"\nBatch size: {batch_size}", end="\n\n")
    print(df, end="\n\n")
    capture = pyshark.LiveCapture(interface='Ethernet')
    capture.apply_on_packets(process_packet)
