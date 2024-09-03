import os
import argparse
import time
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable
import pandas as pd
import pyshark
from pyshark.capture.live_capture import UnknownInterfaceException
import socket
import psutil


uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")


def connect_to_database(uri, username, password, database):
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))
        with driver.session(database=database) as session:
            session.run("RETURN 1")
        return driver
    except ServiceUnavailable:
        raise Exception(f"Unable to connect to Neo4j database. Please check your connection settings.")
    except Exception as e:
        if "database does not exist" in str(e).lower():
            raise Exception(f"{database} database not found. You need to create the default 'captures' database or pass an existing database name.")
        else:
            raise


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
        MERGE (src_ip_address:IP_ADDRESS {IP_ADDRESS: $src_ip_address})
        MERGE (dst_ip_address:IP_ADDRESS {IP_ADDRESS: $dst_ip_address})
        
        MERGE (src_ip_address)-[:PORT]->(src_port:PORT {PORT: $src_port, IP_ADDRESS: $src_ip_address})
        MERGE (dst_ip_address)-[:PORT]->(dst_port:PORT {PORT: $dst_port, IP_ADDRESS: $dst_ip_address})
        
        CREATE (src_port)-[p:PACKET]->(dst_port)
        SET p += { 
            PROTOCOL: $protocol,
            SIZE: $size,
            PAYLOAD: $payload,
            TIMESTAMP: $timestamp
        }
        """, packet_data))


def process_packet(packet, driver, database, local_ip):
    packet_data = {
        "protocol": packet.highest_layer,
        "src_ip_address": packet.ip.src if hasattr(packet, 'ip') else '0.0.0.0',
        "src_port": 0,
        "dst_ip_address": packet.ip.dst if hasattr(packet, 'ip') else '0.0.0.0',
        "dst_port": 0,
        "size": len(packet),
        "payload": None,
        "timestamp": float(packet.sniff_time.timestamp()) * 1000
    }

    if hasattr(packet, 'tcp') or hasattr(packet, 'udp'):
        layer = packet.tcp if hasattr(packet, 'tcp') else packet.udp
        packet_data.update({
            "src_port": int(layer.srcport) if layer.srcport.isdigit() else 0,
            "dst_port": int(layer.dstport) if layer.dstport.isdigit() else 0,
            "payload": layer.payload if hasattr(layer, 'payload') else None
        })

    print(f"[PACKET CAPTURED] {packet_data['src_ip_address']}:{packet_data['src_port']} -> {packet_data['dst_ip_address']}:{packet_data['dst_port']} | {packet_data['protocol']} {packet_data['size']} [PAYLOAD NOT DISPLAYED]")
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
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and store them in a Neo4j database.")
    parser.add_argument("--interface", default="Ethernet", help="Specify the network interface to use (default: Ethernet).")
    parser.add_argument("--file", dest="capture_file", help="Path to the Wireshark capture file.")
    parser.add_argument("--duration", type=int, default=10, help="Specify the duration of the capture in seconds (default: 10).")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to connect to (default: captures).")
    parser.add_argument("--list", action="store_true", help="List available network interfaces.")

    args = parser.parse_args()

    if args.list:
        list_interfaces()
        return

    if args.capture_file and not os.path.isfile(args.capture_file):
        print(f"\nFile not found, please check your file path.", "\n")
        return

    local_ip = get_local_ip()
    print(f"\nLocal IP address: {local_ip}")

    try:
        driver = connect_to_database(uri, username, password, args.database)
    except Exception as e:
        print(f"\n{str(e)}", "\n")
        return

    try:
        if args.capture_file:
            print(f"\nImporting packets from {args.capture_file}", "\n")
            file_capture = pyshark.FileCapture(args.capture_file)
            for packet in file_capture:
                process_packet(packet, driver, args.database, local_ip)
        else:
            interface_capture = pyshark.LiveCapture(interface=args.interface)
            print(f"\nCapturing packets on {args.interface} for {args.duration} seconds", "\n")
            start_time = time.time()
            for packet in interface_capture.sniff_continuously():
                process_packet(packet, driver, args.database, local_ip)
                if time.time() - start_time > args.duration:
                    break
    except UnknownInterfaceException:
        print(f"{args.interface} interface not found. Select an interface from the list below.")
        list_interfaces()
    finally:
        driver.close()

if __name__ == "__main__":
    main()