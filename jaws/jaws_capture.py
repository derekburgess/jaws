import argparse
import hashlib
import os
import socket
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version

from rich.console import Group

from jaws.adapters import SystemClock, UuidCaptureIdGenerator
from jaws.config import CONSOLE, DATABASE
from jaws.domain import (
    CanonicalDigest,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EntityId,
    ObservationScope,
    canonical_json,
    utc_text,
)
from jaws.jaws_utils import (
    Reporter,
    dbms_connection,
    initialize_schema,
    render_activity_panel,
    render_info_panel,
)
from jaws.optional_dependencies import require_module
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


# Every capture/import run is one SESSION, recorded as a CAPTURE node and stamped on
# each of its PACKETs (a property join, like the rest of the packet schema — no
# per-packet edges). Sessions are what make an accumulating graph analyzable: without
# them, profiles aggregate "all traffic ever in this graph" and inter-packet timing
# spans the dead gap between two capture runs, so a real beacon's interval_cv is
# inflated by a 20-minute pause. jaws_compute scopes to a session ('latest' by
# default), which is why drop_database between captures is optional, not required.
def register_capture(driver, database, record):
    """Create one collision-resistant capture and its explicit observation scope."""

    scope = ObservationScope.for_capture(
        record.capture_id,
        record.registered_at,
        perspective=record.perspective,
        filters=(record.capture_filter,) if record.capture_filter else (),
    )
    query = """
    CREATE (capture:CAPTURE {
        CAPTURE_ID: $capture_id,
        LEGACY_CAPTURE_ID: $legacy_capture_id,
        STATE: $state,
        SOURCE_KIND: $source_kind,
        SOURCE_NAME: $source_name,
        CONTENT_SHA256: $content_sha256,
        REGISTERED_AT: datetime($registered_at),
        STARTED_AT: datetime($started_at),
        STARTED: datetime($started_at),
        PACKET_COUNT: 0,
        PACKETS: 0,
        PERSPECTIVE_IP: $perspective_ip,
        CAPTURE_FILTER: $capture_filter,
        TOOL_VERSIONS_JSON: $tool_versions_json,
        SOURCE: $source_name
    })
    CREATE (scope:OBSERVATION_SCOPE {
        SCOPE_ID: $scope_id,
        KIND: $scope_kind,
        CREATED_AT: datetime($registered_at),
        PERSPECTIVE_IP: $perspective_ip,
        FILTERS: $filters
    })
    CREATE (scope)-[:INCLUDES]->(capture)
    """
    assert record.started_at is not None
    parameters = {
        "capture_id": record.capture_id.value,
        "legacy_capture_id": record.legacy_capture_id,
        "state": record.state.value,
        "source_kind": record.source_kind.value,
        "source_name": record.source_name,
        "content_sha256": str(record.content_digest) if record.content_digest else None,
        "registered_at": utc_text(record.registered_at),
        "started_at": utc_text(record.started_at),
        "perspective_ip": record.perspective.value if record.perspective else None,
        "capture_filter": record.capture_filter,
        "tool_versions_json": canonical_json(record.tool_versions),
        "scope_id": scope.scope_id.value,
        "scope_kind": scope.kind.value,
        "filters": list(scope.filters),
    }
    with driver.session(database=database) as session:
        session.run(query, parameters).consume()


def finalize_capture(driver, database, record):
    """Persist one terminal capture snapshot without changing compatibility identity."""

    if record.state not in {
        CaptureState.COMPLETE,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.CANCELLED,
    }:
        raise ValueError("finalize_capture requires a terminal capture record")
    assert record.ended_at is not None
    query = """
    MATCH (c:CAPTURE {CAPTURE_ID: $capture_id})
    SET c.PACKETS = $packet_count,
        c.PACKET_COUNT = $packet_count,
        c.STATE = $state,
        c.ENDED_AT = datetime($ended_at),
        c.FAILURE_CODE = $failure_code
    """
    with driver.session(database=database) as session:
        session.run(
            query,
            {
                "capture_id": record.capture_id.value,
                "packet_count": record.packet_count,
                "state": record.state.value,
                "ended_at": utc_text(record.ended_at),
                "failure_code": record.failure_code,
            },
        ).consume()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as capture_file:
        for chunk in iter(lambda: capture_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return CanonicalDigest(digest.hexdigest())


def capture_tool_versions():
    versions = {}
    for distribution in ("JAWS", "pyshark"):
        try:
            versions[distribution.lower()] = version(distribution)
        except PackageNotFoundError:
            versions[distribution.lower()] = "unknown"
    return versions


def add_packets_to_database(driver, packets_batch, database):
    with driver.session(database=database) as session:
        session.execute_write(
            lambda tx: tx.run(
                """
        UNWIND $packets AS packet
        MERGE (src_ip_address:IP_ADDRESS {IP_ADDRESS: packet.src_ip_address})
        MERGE (dst_ip_address:IP_ADDRESS {IP_ADDRESS: packet.dst_ip_address})

        CREATE (p:PACKET {
            PROTOCOL: packet.protocol,
            SIZE: packet.size,
            PAYLOAD: packet.payload,
            TIMESTAMP: datetime(packet.timestamp),
            CAPTURE_ID: packet.capture_id,
            SRC_IP: packet.src_ip_address,
            DST_IP: packet.dst_ip_address,
            SRC_PORT: packet.src_port,
            DST_PORT: packet.dst_port
        })

        // Port 0 is the placeholder for non-TCP/UDP packets — don't materialize it
        // as a PORT node. The FOREACH-over-CASE is Cypher's conditional write: the
        // list has one element (run the MERGE/CREATE) or none (skip).
        FOREACH (_ IN CASE WHEN packet.src_port <> 0 THEN [1] ELSE [] END |
            MERGE (src_ip_address)-[:PORT]->(src_port:PORT {PORT: packet.src_port, IP_ADDRESS: packet.src_ip_address})
            CREATE (src_port)-[:SENT]->(p)
        )
        FOREACH (_ IN CASE WHEN packet.dst_port <> 0 THEN [1] ELSE [] END |
            MERGE (dst_ip_address)-[:PORT]->(dst_port:PORT {PORT: packet.dst_port, IP_ADDRESS: packet.dst_ip_address})
            CREATE (p)-[:RECEIVED]->(dst_port)
        )
        """,
                packets=packets_batch,
            )
        )


def process_packet(packet):
    # sniff_time is the packet's actual capture time (from the frame header), so
    # imported pcap files keep their original timing — the INTERVAL_MEAN/INTERVAL_CV
    # features measure network cadence, not how fast the file was read. It is a naive
    # local datetime; astimezone() attaches the local zone and converts to UTC.
    sniff_time = getattr(packet, "sniff_time", None)
    timestamp = sniff_time.astimezone(timezone.utc) if sniff_time else datetime.now(timezone.utc)
    packet_data = {
        "protocol": packet.highest_layer,
        "src_ip_address": packet.ip.src if hasattr(packet, "ip") else "0.0.0.0",
        "src_port": 0,
        "dst_ip_address": packet.ip.dst if hasattr(packet, "ip") else "0.0.0.0",
        "dst_port": 0,
        "size": len(packet),
        "payload": None,
        "timestamp": timestamp.isoformat(),
    }

    if hasattr(packet, "tcp") or hasattr(packet, "udp"):
        layer = packet.tcp if hasattr(packet, "tcp") else packet.udp
        packet_data.update(
            {
                "src_port": int(layer.srcport) if layer.srcport.isdigit() else 0,
                "dst_port": int(layer.dstport) if layer.dstport.isdigit() else 0,
                "payload": layer.payload if hasattr(layer, "payload") else None,
            }
        )

    packet_string = f"{packet_data['src_ip_address']}:{packet_data['src_port']} ➜ {packet_data['protocol']}({packet_data['size']}) ➜ {packet_data['dst_ip_address']}:{packet_data['dst_port']}"
    return packet_data, packet_string


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

    capture = None
    capture_record = None
    packets = []
    batch = []
    stored_packet_count = 0
    clock = SystemClock()
    capture_ids = UuidCaptureIdGenerator()

    def flush_batch():
        nonlocal stored_packet_count
        if batch:
            pending = len(batch)
            add_packets_to_database(driver, batch, args.database)
            batch.clear()
            stored_packet_count += pending

    def close_capture():
        if capture is not None:
            try:
                capture.close()
            except Exception:
                pass

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

        source = args.capture_file if args.capture_file else args.interface
        pyshark = require_module("pyshark", "capture", "Packet capture and import")
        registered_at = clock.now()
        capture_id = capture_ids.new()
        source_kind = (
            CaptureSourceKind.PCAP_FILE if args.capture_file else CaptureSourceKind.LIVE_INTERFACE
        )
        active_state = CaptureState.IMPORTING if args.capture_file else CaptureState.RUNNING
        perspective = None if args.capture_file else EntityId(f"ip:{local_ip}")
        content_digest = file_sha256(args.capture_file) if args.capture_file else None
        capture_record = CaptureRecord(
            capture_id=capture_id,
            source_kind=source_kind,
            source_name=source,
            state=CaptureState.REGISTERED,
            registered_at=registered_at,
            legacy_capture_id=registered_at.strftime("%Y%m%dT%H%M%SZ"),
            content_digest=content_digest,
            perspective=perspective,
            tool_versions=capture_tool_versions(),
        ).transition(active_state, clock.now())
        register_capture(driver, args.database, capture_record)

        if args.capture_file:
            config_message = (
                f"Import: {args.capture_file} | perspective unknown | session {capture_id}"
            )
        else:
            config_message = (
                f"Interface: {args.interface} | {local_ip} | {args.duration} seconds | "
                f"session {capture_id}"
            )

        def render():
            return Group(
                render_info_panel("CONFIG", config_message, CONSOLE),
                render_activity_panel("PACKETS", packets, CONSOLE),
            )

        with reporter.activity(render) as update:

            def on_packet(packet):
                packet_data, packet_string = process_packet(packet)
                packet_data["capture_id"] = capture_id.value
                batch.append(packet_data)
                packets.append(packet_string)
                if len(batch) >= BATCH_SIZE:
                    flush_batch()
                update()

            if args.capture_file:
                capture = pyshark.FileCapture(args.capture_file)
                for packet in capture:
                    on_packet(packet)
            else:
                capture = pyshark.LiveCapture(interface=args.interface)
                try:
                    # apply_on_packets enforces a wall-clock timeout, so the capture
                    # ends after `duration` seconds even on a quiet interface (an
                    # elapsed check inside the loop would only run when a packet
                    # arrives, blocking indefinitely with no traffic).
                    capture.apply_on_packets(on_packet, timeout=args.duration)
                except TimeoutError:
                    pass  # the normal end of a timed capture

        flush_batch()
        completed_record = capture_record.transition(
            CaptureState.COMPLETE,
            clock.now(),
            packet_count=stored_packet_count,
        )
        finalize_capture(driver, args.database, completed_record)
        capture_record = completed_record

        reporter.result(
            {
                "database": args.database,
                "source": source,
                "capture_id": capture_id.value,
                "legacy_capture_id": capture_record.legacy_capture_id,
                "packets_captured": stored_packet_count,
            },
            summary=f"Packets({len(packets)}) added to: '{args.database}' as session '{capture_id}'",
        )
        return

    except KeyboardInterrupt:
        if capture_record is not None:
            try:
                flush_batch()
            except Exception:
                pass
            capture_record = capture_record.transition(
                CaptureState.CANCELLED,
                clock.now(),
                packet_count=stored_packet_count,
                failure_code="capture_cancelled",
            )
            finalize_capture(driver, args.database, capture_record)
        reporter.error("CANCELLED", "Capture cancelled.")
        return

    except (MigrationError, ModuleNotFoundError) as e:
        reporter.error("ERROR", str(e))
        return

    except Exception as error:
        if capture_record is not None:
            try:
                flush_batch()
            except Exception:
                pass
            target = CaptureState.PARTIAL if stored_packet_count else CaptureState.FAILED
            capture_record = capture_record.transition(
                target,
                clock.now(),
                packet_count=stored_packet_count,
                failure_code=type(error).__name__,
            )
            try:
                finalize_capture(driver, args.database, capture_record)
            except Exception as finalize_error:
                reporter.info("WARNING", f"Could not finalize failed capture: {finalize_error}")
        reporter.error("ERROR", str(error))
        return

    finally:
        close_capture()
        driver.close()


if __name__ == "__main__":
    main()
