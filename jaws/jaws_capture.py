import os
import argparse
import time
import socket
import psutil
import pyshark
from rich.live import Live
from rich.console import Group
from jaws.jaws_config import *
from jaws.jaws_utils import (
    dbms_connection,
    render_error_panel,
    render_info_panel,
    render_success_panel,
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
    interface_list = []
    for interface, addrs in interfaces.items():
        interface_list.append(f"{interface}")
    return interface_list


def add_packet_to_database(driver, packet_data, database):
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
            TIMESTAMP: datetime()
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
        "payload": None
    }

    if hasattr(packet, 'tcp') or hasattr(packet, 'udp'):
        layer = packet.tcp if hasattr(packet, 'tcp') else packet.udp
        packet_data.update({
            "src_port": int(layer.srcport) if layer.srcport.isdigit() else 0,
            "dst_port": int(layer.dstport) if layer.dstport.isdigit() else 0,
            "payload": layer.payload if hasattr(layer, 'payload') else None
        })

    packet_string = f"{packet_data['src_ip_address']}:{packet_data['src_port']} ➜ {packet_data['dst_ip_address']}:{packet_data['dst_port']} | {packet_data['protocol']}({packet_data['size']}) **PAYLOAD NOT DISPLAYED**"
    add_packet_to_database(driver, packet_data, database)
    return packet_string


def main():
    parser = argparse.ArgumentParser(description="Collect packets from a network interface and stores them in the database.")
    parser.add_argument("--interface", default="Ethernet", help="Specify the network interface to use (default: 'Ethernet').")
    parser.add_argument("--file", dest="capture_file", help="Path to a Wireshark capture file.")
    parser.add_argument("--duration", type=int, default=10, help="Specify the duration of the capture in seconds (default: 10).")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the database to connect to (default: '{DATABASE}').")
    parser.add_argument("--list", action="store_true", help="List available network interfaces.")
    parser.add_argument("--agent", action="store_true", help="Disable rich output for agent use.")
    args = parser.parse_args()
    local_ip = get_local_ip()
    driver = dbms_connection(args.database)
    if driver is None:
        return

    if args.list:
        interface_list = "\n".join(list_interfaces())
        if not args.agent:
            CONSOLE.print(render_info_panel("INTERFACES", interface_list, CONSOLE))
        else:
            print(interface_list)
        return

    if args.capture_file and not os.path.isfile(args.capture_file):
        CONSOLE.print(render_error_panel("ERROR", f"File not found, please check your file path:\n{args.capture_file}", CONSOLE))
        return
    
    if args.interface and not args.capture_file:
        available_interfaces = list_interfaces()
        if args.interface not in available_interfaces:
            CONSOLE.print(render_error_panel("ERROR", f"{args.interface} interface not found.\nSelect an interface from the list below.", CONSOLE))
            CONSOLE.print(render_info_panel("INTERFACES", "\n".join(available_interfaces), CONSOLE))
            driver.close()
            return
        
    packets = []
    try:
        if args.capture_file:
            file_message = f"Import: {args.capture_file} | {local_ip}"
            with Live(Group(
                render_info_panel("CONFIG", file_message, CONSOLE),
                render_activity_panel("PACKETS", packets, CONSOLE)
            ), console=CONSOLE, refresh_per_second=10) as live:
                file_capture = pyshark.FileCapture(args.capture_file)
                for packet in file_capture:
                    packet_string = process_packet(packet, driver, args.database, local_ip)
                    packets.append(packet_string)
                    live.update(Group(
                        render_info_panel("CONFIG", file_message, CONSOLE),
                        render_activity_panel("PACKETS", packets, CONSOLE)
                    ))
        else:
            interface_message = f"Interface: {args.interface} | {local_ip} | {args.duration} seconds"
            if not args.agent:
                with Live(Group(
                    render_info_panel("CONFIG", interface_message, CONSOLE),
                    render_activity_panel("PACKETS", packets, CONSOLE)
                ), console=CONSOLE, refresh_per_second=10) as live:
                    interface_capture = pyshark.LiveCapture(interface=args.interface)
                    start_time = time.time()
                    for packet in interface_capture.sniff_continuously():
                        packet_string = process_packet(packet, driver, args.database, local_ip)
                        packets.append(packet_string)
                        live.update(Group(
                            render_info_panel("CONFIG", interface_message, CONSOLE),
                            render_activity_panel("PACKETS", packets, CONSOLE)
                        ))
                        if time.time() - start_time > args.duration:
                            break
            else:
                interface_capture = pyshark.LiveCapture(interface=args.interface)
                start_time = time.time()
                for packet in interface_capture.sniff_continuously():
                    packet_string = process_packet(packet, driver, args.database, local_ip)
                    packets.append(packet_string)
                    if time.time() - start_time > args.duration:
                        break

        if not args.agent:
            CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Packets({len(packets)}) added to: '{args.database}'", CONSOLE))
        else:
            print(f"[PROCESS COMPLETE] Packets({len(packets)}) added to: '{args.database}'")
        return
    
    finally:
        driver.close()

if __name__ == "__main__":
    main()