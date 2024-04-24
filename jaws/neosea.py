import os
import argparse
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
        MERGE (src:IP {address: $src_ip, src_port: $src_port, src_mac: $src_mac})
        MERGE (dst:IP {address: $dst_ip, dst_port: $dst_port, dst_mac: $dst_mac})
        CREATE (src)-[p:PACKET]->(dst)
        SET p += { 
            protocol: $protocol,  
            size: $size,
            dns_domain: $dns_domain, 
            http_url: $http_url,
            info: $info,
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
        "dns_domain": packet.dns.qry_name if 'DNS' in packet and hasattr(packet.dns, 'qry_name') else None,
        "http_url": packet.http.request.full_uri if 'HTTP' in packet and hasattr(packet.http, 'request') and hasattr(packet.http.request, 'full_uri') else None,
        "info": packet.info if hasattr(packet, 'info') else None,
        "payload": packet.tcp.payload if 'TCP' in packet and hasattr(packet.tcp, 'payload') else None,
        "timestamp": float(packet.sniff_time.timestamp()) * 1000
    }

    if 'IP' in packet:

        packet_data["src_ip"] = packet.ip.src
        packet_data["src_port"] = int(packet[packet.transport_layer].srcport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].srcport.isdigit() else 0

        packet_data["dst_ip"] = packet.ip.dst
        packet_data["dst_port"] = int(packet[packet.transport_layer].dstport) if hasattr(packet, 'transport_layer') and packet.transport_layer and packet[packet.transport_layer].dstport.isdigit() else 0

    print(f" <<<< protocol: {packet_data['protocol']} | src_ip: {packet_data['src_ip']} : {packet_data['src_port']} ( {packet_data['src_mac']} ) dst_ip: {packet_data['dst_ip']} : {packet_data['dst_port']} ( {packet_data['dst_mac']} ) ~size: {packet_data['size']} [PAYLOAD NOT DISPLAYED] {packet_data['timestamp']} >>>> ")

    try:
        add_packet_to_neo4j(driver, packet_data, database)
    except Exception as e:
        print(f"An error occurred: {e}")


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and store them in a Neo4j database")
    parser.add_argument("--interface", default="Ethernet",
                        help="Specify the network interface to use (default: Ethernet)")
    parser.add_argument("--database", default="captures",
                        help="Specify the Neo4j database to connect to (default: captures)")

    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)
    capture = pyshark.LiveCapture(interface=args.interface)
    capture.apply_on_packets(lambda pkt: process_packet(pkt, driver, args.database))

    driver.close()

if __name__ == "__main__":
    main()