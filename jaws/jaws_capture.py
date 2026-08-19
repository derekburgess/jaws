import argparse
import os
import socket
from collections import deque
from ipaddress import ip_address

from rich.console import Group

from jaws.adapters import (
    LivePacketSource,
    PcapPacketSource,
    SystemClock,
    UuidCaptureIdGenerator,
    capture_tool_versions,
)
from jaws.adapters import file_sha256 as _file_sha256
from jaws.config import CONSOLE, DATABASE
from jaws.domain import (
    CanonicalDigest,
    CaptureSourceKind,
    CaptureSpec,
    EntityId,
    PacketObservation,
)
from jaws.jaws_utils import (
    Reporter,
    dbms_connection,
    initialize_schema,
    render_activity_panel,
    render_info_panel,
)
from jaws.optional_dependencies import require_module
from jaws.services import IngestService
from jaws.storage import Neo4jRepositories
from jaws.storage.migrations import MigrationError


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
    psutil = require_module("psutil", "capture", "Network interface discovery")
    interfaces = psutil.net_if_addrs()
    interface_stats = psutil.net_io_counters(pernic=True)
    interface_list = []

    for interface, addrs in interfaces.items():
        if (
            interface in ["lo"]
            or interface.startswith("docker")
            or interface.startswith("tailscale")
        ):
            continue

        if interface in interface_stats:
            stats = interface_stats[interface]
            if stats.bytes_sent > 0 or stats.bytes_recv > 0:
                interface_list.append(f"{interface}")

    return interface_list


BATCH_SIZE = 100


def file_sha256(path):
    """Compatibility alias for the bounded adapter-owned PCAP hash."""

    return CanonicalDigest(_file_sha256(path))


def _perspective(value: str | None) -> EntityId | None:
    if value is None:
        return None
    return EntityId(f"ip:{ip_address(value.strip())}")


def main():
    parser = argparse.ArgumentParser(
        description="Collect packets from a network interface and stores them in the database."
    )
    parser.add_argument(
        "--interface",
        default=None,
        help="Specify the network interface to use (default: the first active interface from --list).",
    )
    parser.add_argument("--file", dest="capture_file", help="Path to a Wireshark capture file.")
    parser.add_argument(
        "--local-ip",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--capture-filter", help=argparse.SUPPRESS)
    parser.add_argument("--display-filter", help=argparse.SUPPRESS)
    parser.add_argument(
        "--duration",
        type=int,
        default=10,
        help="Specify the duration of the capture in seconds (default: 10).",
    )
    parser.add_argument(
        "--database",
        default=DATABASE,
        help=f"Specify the database to connect to (default: '{DATABASE}').",
    )
    parser.add_argument("--list", action="store_true", help="List available network interfaces.")
    args = parser.parse_args()
    reporter = Reporter()

    # Listing interfaces is a purely local operation — resolve it before touching
    # Neo4j so it works (e.g. as the MCP's step 1) even when the database is down.
    if args.list:
        try:
            interfaces = list_interfaces()
        except ModuleNotFoundError as e:
            reporter.error("ERROR", str(e))
            return
        reporter.result({"interfaces": interfaces}, summary="\n".join(interfaces))
        return

    local_ip = get_local_ip()
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    clock = SystemClock()
    capture_ids = UuidCaptureIdGenerator()
    recent_packets: deque[str] = deque(maxlen=10)

    try:
        initialize_schema(driver, args.database, local_ip, reporter)

        if args.capture_file and not os.path.isfile(args.capture_file):
            reporter.error(
                "ERROR", f"File not found, please check your file path:\n{args.capture_file}"
            )
            return

        if not args.capture_file:
            available_interfaces = list_interfaces()
            if args.interface is None:
                if not available_interfaces:
                    reporter.error("ERROR", "No active network interfaces found.")
                    return
                args.interface = available_interfaces[0]
                reporter.info(
                    "CONFIG", f"No interface specified, defaulting to: '{args.interface}'"
                )
            elif args.interface not in available_interfaces:
                reporter.error(
                    "ERROR",
                    f"Interface '{args.interface}' not found. Use list_interfaces to see available interfaces.",
                )
                return

        repositories = Neo4jRepositories.connect(driver, args.database)
        source = args.capture_file if args.capture_file else args.interface
        if source is None:
            raise RuntimeError("capture source was not resolved")
        if args.capture_file and args.capture_filter:
            raise ValueError("--capture-filter is only valid for live interface capture")
        pyshark = require_module("pyshark", "capture", "Packet capture and import")
        declared_filter = args.capture_filter
        if args.display_filter:
            display = f"display={args.display_filter}"
            declared_filter = (
                f"capture={args.capture_filter}; {display}" if args.capture_filter else display
            )
        if args.capture_file:
            perspective = _perspective(args.local_ip)
            perspective_text = perspective.value if perspective else "perspective unknown"
            spec = CaptureSpec(
                source_kind=CaptureSourceKind.PCAP_FILE,
                source_name=source,
                perspective=perspective,
                content_digest=file_sha256(args.capture_file),
                capture_filter=declared_filter,
                tool_versions=capture_tool_versions(),
            )
            config_message = f"Import: {args.capture_file} | {perspective_text}"
        else:
            perspective = _perspective(args.local_ip or local_ip)
            assert perspective is not None
            spec = CaptureSpec(
                source_kind=CaptureSourceKind.LIVE_INTERFACE,
                source_name=source,
                perspective=perspective,
                capture_filter=declared_filter,
                tool_versions=capture_tool_versions(),
            )
            config_message = (
                f"Interface: {args.interface} | {perspective.value} | {args.duration} seconds"
            )

        def render():
            return Group(
                render_info_panel("CONFIG", config_message, CONSOLE),
                render_activity_panel("PACKETS", recent_packets, CONSOLE),
            )

        with reporter.activity(render) as update:

            def on_packet(observation: PacketObservation, summary: str) -> None:
                recent_packets.append(summary)
                update()

            if args.capture_file:
                packet_source = PcapPacketSource(
                    pyshark,
                    args.capture_file,
                    display_filter=args.display_filter,
                    observer=on_packet,
                )
            else:
                assert args.interface is not None
                packet_source = LivePacketSource(
                    pyshark,
                    args.interface,
                    args.duration,
                    capture_filter=args.capture_filter,
                    display_filter=args.display_filter,
                    observer=on_packet,
                )
            capture_record = IngestService(
                repositories.captures,
                repositories.packets,
                clock,
                capture_ids,
                batch_size=BATCH_SIZE,
            ).ingest(spec, packet_source)

        reporter.result(
            {
                "database": args.database,
                "source": source,
                "capture_id": capture_record.capture_id.value,
                "legacy_capture_id": capture_record.legacy_capture_id,
                "packets_captured": capture_record.packet_count,
            },
            summary=(
                f"Packets({capture_record.packet_count}) added to: '{args.database}' "
                f"as session '{capture_record.capture_id}'"
            ),
        )
        return

    except KeyboardInterrupt:
        reporter.error("CANCELLED", "Capture cancelled.")
        return

    except (MigrationError, ModuleNotFoundError) as e:
        reporter.error("ERROR", str(e))
        return

    except Exception as error:
        reporter.error("ERROR", str(error))
        return

    finally:
        driver.close()


if __name__ == "__main__":
    main()
