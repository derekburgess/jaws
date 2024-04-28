import os
import argparse
import time
from neo4j import GraphDatabase
import pandas as pd
import pyshark


uri = os.getenv("LOCAL_NEO4J_URI")
username = os.getenv("LOCAL_NEO4J_USERNAME")
password = os.getenv("LOCAL_NEO4J_PASSWORD")


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
        "src_ip": None,
        "src_port": None,
        "src_mac": packet.eth.src if 'ETH' in packet else None,
        "dst_ip": None,
        "dst_port": None,
        "dst_mac": packet.eth.dst if 'ETH' in packet else None,
        "size": len(packet),
        "payload": packet.tcp.payload if 'TCP' in packet and hasattr(packet.tcp, 'payload') else None,
        "timestamp": float(packet.sniff_time.timestamp()) * 1000
    }

    if 'IP' in packet:

        packet_data["src_ip"] = packet.ip.src
        packet_data["src_port"] = int(packet[packet.transport_layer].srcport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].srcport.isdigit() else 0

        packet_data["dst_ip"] = packet.ip.dst
        packet_data["dst_port"] = int(packet[packet.transport_layer].dstport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].dstport.isdigit() else 0

    print(f"<<< PACKET CAPTURED {packet_data['src_ip']}:{packet_data['src_port']}({packet_data['src_mac']}) -> {packet_data['dst_ip']}:{packet_data['dst_port']}({packet_data['dst_mac']}) {packet_data['protocol']} {packet_data['size']} [PAYLOAD NOT DISPLAYED] >>>")

    try:
        add_packet_to_neo4j(driver, packet_data, database)
    except Exception as e:
        print(f"An error occurred: {e}")


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and store them in a Neo4j database")
    parser.add_argument("--interface", default="Ethernet",
                        help="Specify the network interface to use (default: Ethernet)")
    parser.add_argument("--duration", type=int, default=10,
                        help="Specify the duration of the capture in seconds (default: 10)")
    parser.add_argument("--database", default="captures",
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)
    capture = pyshark.LiveCapture(interface=args.interface)

    start_time = time.time()
    for packet in capture.sniff_continuously():
        process_packet(packet, driver, args.database)
        if time.time() - start_time > args.duration:
            break

    driver.close()


if __name__ == "__main__":
    main()