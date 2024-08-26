import os
import argparse
import time
from neo4j import GraphDatabase
import pandas as pd
import pyshark
import socket
import psutil


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")


def connect_to_database(uri, username, password, database):
    return GraphDatabase.driver(uri, auth=(username, password))


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"


def add_packet_to_neo4j(driver, packet_data, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("""
        MERGE (src:IP {address: $src_ip})
        MERGE (dst:IP {address: $dst_ip})
        CREATE (src)-[p:PACKET]->(dst)
        SET p += { 
            protocol: $protocol,
            src_port: $src_port,
            dst_port: $dst_port,
            src_mac: $src_mac,
            dst_mac: $dst_mac,
            size: $size,
            payload: $payload, 
            timestamp: $timestamp
        }
        """, packet_data))


def process_packet(packet, driver, database, local_ip):
    packet_data = {
        "protocol": packet.highest_layer,
        "src_ip": packet.ip.src if hasattr(packet, 'ip') else '0.0.0.0',
        "src_port": 0,
        "src_mac": packet.eth.src if hasattr(packet, 'eth') else '00:00:00:00:00:00',
        "dst_ip": packet.ip.dst if hasattr(packet, 'ip') else '0.0.0.0',
        "dst_port": 0,
        "dst_mac": packet.eth.dst if hasattr(packet, 'eth') else '00:00:00:00:00:00',
        "size": len(packet),
        "payload": None,
        "timestamp": float(packet.sniff_time.timestamp()) * 1000
    }

    if hasattr(packet, 'tcp'):
        packet_data.update({
            "src_port": int(packet.tcp.srcport) if packet.tcp.srcport.isdigit() else 0,
            "dst_port": int(packet.tcp.dstport) if packet.tcp.dstport.isdigit() else 0,
            "payload": packet.tcp.payload if hasattr(packet.tcp, 'payload') else None
        })
    elif hasattr(packet, 'udp'):
        packet_data.update({
            "src_port": int(packet.udp.srcport) if packet.udp.srcport.isdigit() else 0,
            "dst_port": int(packet.udp.dstport) if packet.udp.dstport.isdigit() else 0,
            "payload": packet.udp.payload if hasattr(packet.udp, 'payload') else None
        })

    print(f"<<< [PACKET CAPTURED] {packet_data['src_ip']}:{packet_data['src_port']} ({packet_data['src_mac']}) -> {packet_data['dst_ip']}:{packet_data['dst_port']} ({packet_data['dst_mac']}) <> {packet_data['protocol']} {packet_data['size']} [PAYLOAD NOT DISPLAYED] >>>")
    try:
        add_packet_to_neo4j(driver, packet_data, database)
    except Exception as e:
        print(f"Failed to add packet to Neo4j: {e}")


def list_interfaces():
    interfaces = psutil.net_if_addrs()
    print("\nAvailable network interfaces:")
    for interface, addrs in interfaces.items():
        print(f"- {interface}")
    print("\n")
    exit(0)


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and store them in a Neo4j database")
    parser.add_argument("--interface", default="Ethernet", help="Specify the network interface to use (default: Ethernet)")
    parser.add_argument("--file", dest="capture_file", help="Path to the Wireshark capture file")
    parser.add_argument("--duration", type=int, default=10, help="Specify the duration of the capture in seconds (default: 10)")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures)")
    parser.add_argument("--list", action="store_true", help="List available network interfaces.")

    args = parser.parse_args()

    if args.list:
        list_interfaces()

    local_ip = get_local_ip()
    print(f"Local IP address: {local_ip}")

    driver = connect_to_database(uri, username, password, args.database)
    interface_capture = pyshark.LiveCapture(interface=args.interface)
    
    if args.capture_file:
        print(f"\nImporting packets from {args.capture_file}", "\n")
        file_capture = pyshark.FileCapture(args.capture_file)
        for packet in file_capture:
            process_packet(packet, driver, args.database, local_ip)
    else:
        print(f"\nCapturing packets on {args.interface} for {args.duration} seconds", "\n")
        start_time = time.time()
        for packet in interface_capture.sniff_continuously():
            process_packet(packet, driver, args.database, local_ip)
            if time.time() - start_time > args.duration:
                break

    driver.close()

if __name__ == "__main__":
    main()