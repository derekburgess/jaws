"""JAWS MCP Server — exposes JAWS network-monitoring tools via FastMCP stdio."""

from mcp.server.fastmcp import FastMCP
import subprocess
import sys
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

ROOT = Path(__file__).parent.parent   # /path/to/jaws/
SCRIPTS = ROOT / "jaws"

mcp = FastMCP("JAWS - Wireshark MCP with Network Analysis Tools")

def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        return f"ERROR (exit {result.returncode}): {result.stderr.strip()}"
    return result.stdout.strip() or "(no output)"

@mcp.tool(name="list_interfaces", description="List available network interfaces. Never select virtual or loopback interfaces such as 'lo' and 'docker0'.")
def list_interfaces() -> str:
    return _run([sys.executable, str(SCRIPTS / "jaws_capture.py"), "--list", "--agent"])

@mcp.tool(name="capture_packets", description="Capture packets into the database. Pass a duration in seconds depending on the amount of data you want to capture.")
def capture_packets(interface: str, duration: int) -> str:
    return _run([
        sys.executable, str(SCRIPTS / "jaws_capture.py"),
        "--interface", interface,
        "--duration", str(duration),
        "--agent",
    ])

@mcp.tool(name="document_organizations", description="Enrich data with organization ownership by looking up IP addresses.")
def document_organizations() -> str:
    return _run([sys.executable, str(SCRIPTS / "jaws_ipinfo.py"), "--agent"])

@mcp.tool(name="compute_embeddings", description="Transform the network traffic data into embeddings, improving the quality of the data for downstream analysis.")
def compute_embeddings() -> str:
    return _run([sys.executable, str(SCRIPTS / "jaws_compute.py"), "--agent"])

@mcp.tool(name="anomaly_detection", description="Analyze the network traffic data and embeddings and return a list of anomalies.")
def anomaly_detection() -> str:
    return _run([sys.executable, str(SCRIPTS / "jaws_finder.py"), "--agent"])

@mcp.tool(name="drop_database", description="Clear the database of all data.")
def drop_database() -> str:
    return _run([sys.executable, str(SCRIPTS / "jaws_utils.py"), "--agent"])

_driver = None

def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        uri = os.environ["NEO4J_URI"]
        user = os.environ["NEO4J_USERNAME"]
        password = os.environ["NEO4J_PASSWORD"]
        _driver = GraphDatabase.driver(uri, auth=(user, password))
    return _driver

@mcp.tool(name="fetch_traffic", description=(
    "Fetch data from the database and return it as a string. "
    "Pass a duration in minutes to time-limit the data. "
    "Pass a limit to control the number of entries returned."
))
def fetch_traffic(duration: int, limit: int) -> str:
    database = os.environ.get("NEO4J_DATABASE", "captures")
    query = """
    MATCH (traffic:TRAFFIC)
    WHERE traffic.TIMESTAMP > datetime() - duration({minutes: $duration})
    RETURN DISTINCT
        traffic.SRC_IP_ADDRESS AS src_ip_address,
        traffic.SRC_PORT AS src_port,
        traffic.DST_IP_ADDRESS AS dst_ip_address,
        traffic.DST_PORT AS dst_port,
        traffic.PROTOCOL AS protocol,
        traffic.ORGANIZATION AS org,
        traffic.HOSTNAME AS hostname,
        traffic.LOCATION AS location,
        traffic.TOTAL_SIZE AS total_size,
        traffic.OUTLIER AS outlier,
        traffic.TIMESTAMP AS timestamp
    ORDER BY traffic.TIMESTAMP DESC
    LIMIT $limit
    """
    driver = _get_driver()
    with driver.session(database=database) as session:
        result = session.run(query, duration=duration, limit=limit)
        data = [dict(record) for record in result]
    return str(data)

"""
# ---------------------------------------------------------------------------
# Tool 8: send_email
# ---------------------------------------------------------------------------

@mcp.tool(description="Send an email to High Command with the entire contents of the report.")
def send_email(content: str) -> str:
    sender = os.environ["EMAIL_SENDER"]
    recipient = os.environ["EMAIL_RECIPIENT"]
    password = os.environ["EMAIL_PASSWORD"]
    server_host = os.environ["EMAIL_SERVER"]
    port = int(os.environ["EMAIL_PORT"])

    message = MIMEMultipart()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Situation Report"
    body = (
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{content}\n\n"
        "** This is an automated report from the JAWS Network Monitoring System. **"
    )
    message.attach(MIMEText(body, "plain"))

    with smtplib.SMTP(server_host, port) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(message)

    return f"Report emailed to: {recipient}"

"""

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sse", action="store_true")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.sse:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
