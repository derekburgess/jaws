import os
from neo4j import GraphDatabase
import pandas as pd
import pyshark


uri = "bolt://localhost:7687"  # Typical/local Neo4j URI - Updated as needed
username = "neo4j"  # Typical/local Neo4j username - Updated as needed
password = "testtest"  # Typical/l Neo4j password - Updated as needed
driver = GraphDatabase.driver(uri, auth=(username, password))
chum_addr = os.getenv("CHUM_ADDR")  # Flag an IP address to be labeled as "chum"
df = pd.DataFrame(columns=['protocol', 'tcp_flags', 'src_ip', 'src_port', 'src_mac', 'dst_ip', 'dst_port', 'dst_mac', 'size', 'dns_domain', 'http_url', 'info', 'payload', 'timestamp', 'label'])


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


def add_packet_to_neo4j(driver, packet_data):
    with driver.session(database="ethcaptures") as session: # Update database="" to your database name
        session.execute_write(lambda tx: tx.run("""
        MERGE (src:IP {address: $src_ip})
        MERGE (dst:IP {address: $dst_ip})
        CREATE (src)-[p:PACKET]->(dst)
        SET p += { 
            protocol: $protocol, 
            tcp_flags: $tcp_flags,  
            src_port: $src_port, 
            src_mac: $src_mac, 
            dst_port: $dst_port, 
            dst_mac: $dst_mac, 
            size: $size,
            dns_domain: $dns_domain, 
            http_url: $http_url,
            info: $info,
            payload: $payload, 
            timestamp: $timestamp, 
            label: $label
        }
        """, packet_data))


def process_packet(packet):
    global df, packet_id
    packet_data = {
        "protocol": packet.transport_layer if hasattr(packet, 'transport_layer') else 'None',
        "tcp_flags": convert_hex_tcp_flags(packet.tcp.flags) if 'TCP' in packet else 'None',
        "src_ip": '0.0.0.0',
        "src_port": 0,
        "src_mac": packet.eth.src if 'ETH' in packet else 'None',
        "dst_ip": '0.0.0.0',
        "dst_port": 0,
        "dst_mac": packet.eth.dst if 'ETH' in packet else 'None',
        "size": len(packet),
        "dns_domain": packet.dns.qry_name if 'DNS' in packet and hasattr(packet.dns, 'qry_name') else 'None',
        "http_url": packet.http.request.full_uri if 'HTTP' in packet and hasattr(packet.http, 'request') and hasattr(packet.http.request, 'full_uri') else 'None',
        "info": packet.info if hasattr(packet, 'info') else 'None',
        "payload": packet.tcp.payload if 'TCP' in packet and hasattr(packet.tcp, 'payload') else 'None',
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
        print(f">>>> [PROTOCOL: {packet_data['protocol']} | {packet_data['tcp_flags']}] [SRC: {packet_data['src_ip']} | {packet_data['src_port']} | {packet_data['src_mac']}] [DST :{packet_data['dst_ip']} | {packet_data['dst_port']} | {packet_data['dst_mac']}] [SIZE :{packet_data['size']}] [DNS: {packet_data['dns_domain']}] [HTTP: {packet_data['http_url']}] [INFO: {packet_data['info']}] [PAYLOAD NOT DISPLAYED] [{packet_data['timestamp']}] <<<< {packet_data['label']} PACKET")
    else:
        print(f">>>> [PROTOCOL: {packet_data['protocol']} | {packet_data['tcp_flags']}] [SRC: {packet_data['src_ip']} | {packet_data['src_port']} | {packet_data['src_mac']}] [DST :{packet_data['dst_ip']} | {packet_data['dst_port']} | {packet_data['dst_mac']}] [SIZE :{packet_data['size']}] [DNS: {packet_data['dns_domain']}] [HTTP: {packet_data['http_url']}] [INFO: {packet_data['info']}] [PAYLOAD NOT DISPLAYED] [{packet_data['timestamp']}] <<<< {packet_data['label']} PACKET")

    try:
        add_packet_to_neo4j(driver, packet_data) # Calls the function which calls the driver and the database name.
    except Exception as e:
        print(f"An error occurred: {e}")


def main():
    print(df, end="\n\n")
    capture = pyshark.LiveCapture(interface='Ethernet')
    capture.apply_on_packets(process_packet)

    driver.close()

if __name__ == "__main__":
    main()