import os
import argparse
import time
from neo4j import GraphDatabase
import pandas as pd
import pyshark


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")


def connect_to_database(uri, username, password, database):
    return GraphDatabase.driver(uri, auth=(username, password))


def add_packet_to_neo4j(driver, packet_data, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("""
        MERGE (src:SRC_IP {src_address: $src_ip, src_port: $src_port, src_mac: $src_mac})
        MERGE (dst:DST_IP {dst_address: $dst_ip, dst_port: $dst_port, dst_mac: $dst_mac})
        CREATE (src)-[p:PACKET]->(dst)
        SET p += { 
            protocol: $protocol,  
            size: $size,
            payload: $payload, 
            timestamp: $timestamp
        }
        """, packet_data))


def process_packet(packet, driver, database):
    packet_data = {
        "protocol": packet.highest_layer,
        "src_ip": '0.0.0.0',
        "src_port": 0,
        "src_mac": '00:00:00:00:00:00',
        "dst_ip": '0.0.0.0',
        "dst_port": 0,
        "dst_mac": '00:00:00:00:00:00',
        "size": len(packet),
        "payload": 'No Payload',
        "timestamp": float(packet.sniff_time.timestamp()) * 1000
    }

    if 'IP' in packet:
        packet_data["src_ip"] = packet.ip.src
        packet_data["dst_ip"] = packet.ip.dst

    if 'ETH' in packet:
        packet_data["src_mac"] = packet.eth.src
        packet_data["dst_mac"] = packet.eth.dst

    if 'TCP' in packet:
        packet_data["src_port"] = int(packet.tcp.srcport) if packet.tcp.srcport.isdigit() else 0
        packet_data["dst_port"] = int(packet.tcp.dstport) if packet.tcp.dstport.isdigit() else 0
        packet_data["payload"] = packet.tcp.payload if hasattr(packet.tcp, 'payload') else None

    if 'UDP' in packet:
        packet_data["src_port"] = int(packet.udp.srcport) if packet.udp.srcport.isdigit() else 0
        packet_data["dst_port"] = int(packet.udp.dstport) if packet.udp.dstport.isdigit() else 0
        packet_data["payload"] = packet.udp.payload if hasattr(packet.udp, 'payload') else None

    print(f"<<< [PACKET CAPTURED] {packet_data['src_ip']}:{packet_data['src_port']} ({packet_data['src_mac']}) -> {packet_data['dst_ip']}:{packet_data['dst_port']} ({packet_data['dst_mac']}) <> {packet_data['protocol']} {packet_data['size']} [PAYLOAD NOT DISPLAYED] >>>")

    try:
        add_packet_to_neo4j(driver, packet_data, database)
    except Exception as e:
        print(f"An error occurred: {e}")


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and store them in a Neo4j database")
    parser.add_argument("--interface", default="Ethernet",
                        help="Specify the network interface to use (default: Ethernet)")
    parser.add_argument("--file", dest="capture_file", help="Path to the Wireshark capture file")
    parser.add_argument("--duration", type=int, default=10,
                        help="Specify the duration of the capture in seconds (default: 10)")
    parser.add_argument("--database", default="captures",
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)
    interface_capture = pyshark.LiveCapture(interface=args.interface)
    
    if args.capture_file:
        print(f"\nImporting packets from {args.capture_file}", "\n")
        file_capture = pyshark.FileCapture(args.capture_file)
        for packet in file_capture:
            process_packet(packet, driver, args.database)
    else:
        print(f"\nCapturing packets on {args.interface} for {args.duration} seconds", "\n")
        start_time = time.time()
        for packet in interface_capture.sniff_continuously():
            process_packet(packet, driver, args.database)
            if time.time() - start_time > args.duration:
                break

    driver.close()


if __name__ == "__main__":
    main()