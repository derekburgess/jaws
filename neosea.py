import pyshark
import pandas as pd
import os
from neo4j import GraphDatabase

uri = "bolt://localhost:7687" # Update as needed...
username = "neo4j" # Local Neo4j username
password = "testtest" # Local Neo4j password
driver = GraphDatabase.driver(uri, auth=(username, password))
batch_size = 1000 # CSV batch size, considered a backup, or if you prefer working with CSV... however, use the CSV directory for that.
chum_addr = 'AWS IP ADDR'  # AWS IP address of the chum server
df = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'dns_domain', 'http_url', 'info', 'payload', 'timestamp', 'label'])

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

def add_packet_to_neo4j(tx, packet_data):
    query = """
    MERGE (src:IP {address: $src_ip})
    MERGE (dst:IP {address: $dst_ip})
    CREATE (src)-[p:PACKET]->(dst)
    SET p.packet_id = $packet_id, 
        p.protocol = $protocol, 
        p.tcp_flags = $tcp_flags,  
        p.src_port = $src_port, 
        p.src_mac = $src_mac, 
        p.dst_port = $dst_port, 
        p.dst_mac = $dst_mac, 
        p.size = $size,
        p.dns_domain = $dns_domain, 
        p.http_url = $http_url,
        p.info = $info,
        p.payload = $payload, 
        p.timestamp = $timestamp, 
        p.label = $label
    """
    tx.run(query, packet_id=packet_data["packet_id"], protocol=packet_data["protocol"], tcp_flags=packet_data["tcp_flags"], dns_domain=packet_data["dns_domain"], http_url=packet_data["http_url"], src_ip=packet_data["src_ip"], src_port=packet_data["src_port"], src_mac=packet_data["src_mac"], dst_ip=packet_data["dst_ip"], dst_port=packet_data["dst_port"], dst_mac=packet_data["dst_mac"], size=packet_data["size"], info=packet_data["info"], payload=packet_data["payload"], timestamp=packet_data["timestamp"], label=packet_data["label"])

def process_packet(packet):
    global df, packet_id
    packet_data = {
        "packet_id": packet_id,
        "protocol": packet.transport_layer if hasattr(packet, 'transport_layer') else 'UNKNOWN',
        "tcp_flags": convert_hex_tcp_flags(packet.tcp.flags) if 'TCP' in packet else 'NA',
        "src_ip": '0.0.0.0',
        "src_port": 0,
        "src_mac": packet.eth.src if 'ETH' in packet else 'NA',
        "dst_ip": '0.0.0.0',
        "dst_port": 0,
        "dst_mac": packet.eth.dst if 'ETH' in packet else 'NA',
        "size": len(packet),
        "dns_domain": packet.dns.qry_name if 'DNS' in packet and hasattr(packet.dns, 'qry_name') else 'NA',
        "http_url": packet.http.request.full_uri if 'HTTP' in packet and hasattr(packet.http, 'request') and hasattr(packet.http.request, 'full_uri') else 'NA',
        "info": packet.info if hasattr(packet, 'info') else 'NA',
        "payload": packet.tcp.payload if 'TCP' in packet and hasattr(packet.tcp, 'payload') else 'NA',
        "timestamp": float(packet.sniff_time.timestamp()) * 1000,
        "label": 'BASE'
    }

    if 'IP' in packet:
        packet_data["src_ip"] = packet.ip.src
        packet_data["dst_ip"] = packet.ip.dst
        packet_data["src_port"] = int(packet[packet.transport_layer].srcport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].srcport.isdigit() else 0
        packet_data["dst_port"] = int(packet[packet.transport_layer].dstport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].dstport.isdigit() else 0
        packet_data["label"] = 'CHUM' if packet.ip.dst == chum_addr else 'BASE'

    if packet_data['dst_ip'] == chum_addr:
        print(f">>>> [{packet_data['packet_id']}] [PROTOCOL: {packet_data['protocol']} | {packet_data['tcp_flags']}] [SRC: {packet_data['src_ip']} | {packet_data['src_port']} | {packet_data['src_mac']}] [DST :{packet_data['dst_ip']} | {packet_data['dst_port']} | {packet_data['dst_mac']}] [SIZE :{packet_data['size']}] [DNS: {packet_data['dns_domain']}] [HTTP: {packet_data['http_url']}] [INFO: {packet_data['info']}] [PAYLOAD NOT DISPLAYED] [{packet_data['timestamp']}] <<<< {packet_data['label']} PACKET")
    else:
        print(f">>>> [{packet_data['packet_id']}] [PROTOCOL: {packet_data['protocol']} | {packet_data['tcp_flags']}] [SRC: {packet_data['src_ip']} | {packet_data['src_port']} | {packet_data['src_mac']}] [DST :{packet_data['dst_ip']} | {packet_data['dst_port']} | {packet_data['dst_mac']}] [SIZE :{packet_data['size']}] [DNS: {packet_data['dns_domain']}] [HTTP: {packet_data['http_url']}] [INFO: {packet_data['info']}] [PAYLOAD NOT DISPLAYED] [{packet_data['timestamp']}] <<<< {packet_data['label']} PACKET")

    new_row = pd.Series(packet_data, name='x')
    df = pd.concat([df, pd.DataFrame(new_row).T], ignore_index=True)

    if len(df) >= batch_size:
        try:
            existing_data = pd.read_csv('./data/packets.csv')
        except (FileNotFoundError, pd.errors.EmptyDataError):
            existing_data = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'dns_domain', 'http_url', 'info', 'payload', 'timestamp', 'label'])

        combined_data = pd.concat([existing_data, df], ignore_index=True)
        combined_data.to_csv('./data/packets.csv', index=False, na_rep='NA')
        print(f"\nSaved {batch_size} packets to CSV file.", "\n")
        df = pd.DataFrame(columns=['packet_id', 'protocol', 'tcp_flags', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'dns_domain', 'http_url', 'info', 'payload', 'timestamp', 'label'])

    try:
        with driver.session() as session:
            session.execute_write(add_packet_to_neo4j, packet_data)
    except Exception as e:
        print(f"An error occurred: {e}")

    packet_id += 1

if __name__ == "__main__":
    print(f"\nBatch size: {batch_size}", end="\n\n")
    print(df, end="\n\n")
    capture = pyshark.LiveCapture(interface='Ethernet')
    capture.apply_on_packets(process_packet)

driver.close()