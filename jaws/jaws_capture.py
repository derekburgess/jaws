import os
import argparse
import time
import socket
from datetime import datetime, timezone
import psutil
import pyshark
from rich.console import Group
from jaws.config import CONSOLE, DATABASE
from jaws.jaws_utils import (
    dbms_connection,
    initialize_schema,
    Reporter,
    render_info_panel,
    render_activity_panel
)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"
    

def list_interfaces():
    interfaces = psutil.net_if_addrs()
    interface_stats = psutil.net_io_counters(pernic=True)
    interface_list = []
    
    for interface, addrs in interfaces.items():
        if interface in ['lo'] or interface.startswith('docker') or interface.startswith('tailscale'):
            continue
            
        if interface in interface_stats:
            stats = interface_stats[interface]
            if stats.bytes_sent > 0 or stats.bytes_recv > 0:
                interface_list.append(f"{interface}")
    
    return interface_list


BATCH_SIZE = 100


def add_packets_to_database(driver, packets_batch, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("""
        UNWIND $packets AS packet
        MERGE (src_ip_address:IP_ADDRESS {IP_ADDRESS: packet.src_ip_address})
        MERGE (dst_ip_address:IP_ADDRESS {IP_ADDRESS: packet.dst_ip_address})

        MERGE (src_ip_address)-[:PORT]->(src_port:PORT {PORT: packet.src_port, IP_ADDRESS: packet.src_ip_address})
        MERGE (dst_ip_address)-[:PORT]->(dst_port:PORT {PORT: packet.dst_port, IP_ADDRESS: packet.dst_ip_address})

        CREATE (p:PACKET {
            PROTOCOL: packet.protocol,
            SIZE: packet.size,
            PAYLOAD: packet.payload,
            TIMESTAMP: datetime(packet.timestamp),
            SRC_IP: packet.src_ip_address,
            DST_IP: packet.dst_ip_address,
            SRC_PORT: packet.src_port,
            DST_PORT: packet.dst_port
        })

        CREATE (src_port)-[:SENT]->(p)
        CREATE (p)-[:RECEIVED]->(dst_port)
        """, packets=packets_batch))


def process_packet(packet, local_ip):
    packet_data = {
        "protocol": packet.highest_layer,
        "src_ip_address": packet.ip.src if hasattr(packet, 'ip') else '0.0.0.0',
        "src_port": 0,
        "dst_ip_address": packet.ip.dst if hasattr(packet, 'ip') else '0.0.0.0',
        "dst_port": 0,
        "size": len(packet),
        "payload": None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    if hasattr(packet, 'tcp') or hasattr(packet, 'udp'):
        layer = packet.tcp if hasattr(packet, 'tcp') else packet.udp
        packet_data.update({
            "src_port": int(layer.srcport) if layer.srcport.isdigit() else 0,
            "dst_port": int(layer.dstport) if layer.dstport.isdigit() else 0,
            "payload": layer.payload if hasattr(layer, 'payload') else None
        })

    packet_string = f"{packet_data['src_ip_address']}:{packet_data['src_port']} ➜ {packet_data['dst_ip_address']}:{packet_data['dst_port']} | {packet_data['protocol']}({packet_data['size']})"
    return packet_data, packet_string


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and stores them in the database.")
    parser.add_argument("--interface", default="Ethernet", help="Specify the network interface to use (default: 'Ethernet').")
    parser.add_argument("--file", dest="capture_file", help="Path to a Wireshark capture file.")
    parser.add_argument("--duration", type=int, default=10, help="Specify the duration of the capture in seconds (default: 10).")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--list", action="store_true", help="List available network interfaces.")
    args = parser.parse_args()
    reporter = Reporter()
    local_ip = get_local_ip()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    capture = None
    packets = []
    batch = []

    def flush_batch():
        if batch:
            add_packets_to_database(driver, batch, args.database)
            batch.clear()

    def close_capture():
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass

    try:
        if args.list:
            interfaces = list_interfaces()
            reporter.result({"interfaces": interfaces}, summary="\n".join(interfaces))
            return

        initialize_schema(driver, args.database, local_ip, reporter)

        if args.capture_file and not os.path.isfile(args.capture_file):
            reporter.error("ERROR", f"File not found, please check your file path:\n{args.capture_file}")
            return

        if args.interface and not args.capture_file:
            available_interfaces = list_interfaces()
            if args.interface not in available_interfaces:
                reporter.error("ERROR", f"Interface '{args.interface}' not found. Use list_interfaces to see available interfaces.")
                return

        if args.capture_file:
            config_message = f"Import: {args.capture_file} | {local_ip}"
        else:
            config_message = f"Interface: {args.interface} | {local_ip} | {args.duration} seconds"

        def render():
            return Group(
                render_info_panel("CONFIG", config_message, CONSOLE),
                render_activity_panel("PACKETS", packets, CONSOLE)
            )

        with reporter.activity(render) as update:
            if args.capture_file:
                capture = pyshark.FileCapture(args.capture_file)
                packet_source = capture
            else:
                capture = pyshark.LiveCapture(interface=args.interface)
                packet_source = capture.sniff_continuously()

            start_time = time.time()
            for packet in packet_source:
                packet_data, packet_string = process_packet(packet, local_ip)
                batch.append(packet_data)
                packets.append(packet_string)
                if len(batch) >= BATCH_SIZE:
                    flush_batch()
                update()
                if not args.capture_file and time.time() - start_time > args.duration:
                    break

        flush_batch()

        source = args.capture_file if args.capture_file else args.interface
        reporter.result(
            {"database": args.database, "source": source, "packets_captured": len(packets)},
            summary=f"Packets({len(packets)}) added to: '{args.database}'",
        )
        return

    finally:
        flush_batch()
        close_capture()
        driver.close()

if __name__ == "__main__":
    main()