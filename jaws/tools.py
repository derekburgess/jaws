from datetime import datetime
import subprocess
import pandas as pd

from typing import Annotated
from semantic_kernel.functions import kernel_function

from jaws.jaws_config import *
from jaws.jaws_utils import *

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

driver = dbms_connection(DATABASE)


class ListInterfaces:
    @kernel_function(description="List available network interfaces. You will never want to select any virtual or loopback interfaces such as; 'lo' and 'docker0'.")
    def list_interfaces(self) -> Annotated[str, "A list of available network interfaces."]:
        CONSOLE.print(render_info_panel("TOOL", "Checking for available network interfaces.", CONSOLE))
        interfaces = subprocess.run(['python', './jaws/jaws_capture.py', '--list', '--agent'], capture_output=True, text=True)
        return str(interfaces.stdout)


class CapturePackets:
    @kernel_function(description="Captures packets into the database. Pass a duration in seconds depending on the amount of data you want to capture.")
    def capture_packets(self, interface: str, duration: int) -> Annotated[str, "A system message once the process is complete."]:
        CONSOLE.print(render_info_panel("TOOL", f"Capturing network traffic on '{interface}' for {duration} seconds.", CONSOLE))
        packets = subprocess.run(['python', './jaws/jaws_capture.py', '--interface', interface, '--duration', str(duration), '--agent'], capture_output=True, text=True)
        return str(packets.stdout)


class DocumentOrganizations:
    @kernel_function(description="Enriches data with organization ownership by looking up IP addresses.")
    def document_organizations(self) -> Annotated[str, "A system message once the process is complete."]:
        CONSOLE.print(render_info_panel("TOOL", "Enriching data with organization ownership.", CONSOLE))
        organizations = subprocess.run(['python', './jaws/jaws_ipinfo.py', '--agent'], capture_output=True, text=True)
        return str(organizations.stdout)


class ComputeEmbeddings:
    @kernel_function(description="Transforms the network traffic data into embeddings, improving the quality of the data for downstream analysis.")
    def compute_embeddings(self) -> Annotated[str, "A system message once the process is complete."]:
        CONSOLE.print(render_info_panel("TOOL", "Transforming data into embeddings.", CONSOLE))
        embeddings = subprocess.run(['python', './jaws/jaws_compute.py', '--agent'], capture_output=True, text=True)
        return str(embeddings.stdout)
    

class AnomalyDetection:
    @kernel_function(description="Analyzes the network traffic data and embeddings and returns a list of anomalies.")
    def anomoly_detection(self) -> Annotated[str, "A string containing a list of anomalies."]:
        CONSOLE.print(render_info_panel("TOOL", "Analyzing data for anomalies.", CONSOLE))
        output = subprocess.run(['python', './jaws/jaws_finder.py', '--agent'], capture_output=True, text=True)
        CONSOLE.print(render_assistant_panel("OUTPUT", str(output.stdout), CONSOLE))
        return str(output.stdout)
    

class DropDatabase:
    @kernel_function(description="Clears the database of all data.")
    def drop_database(self) -> Annotated[str, "A system message once the process is complete."]:
        CONSOLE.print(render_info_panel("TOOL", "Cleaning up the database.", CONSOLE))
        output = subprocess.run(['python', './jaws/jaws_utils.py', '--agent'], capture_output=True, text=True)
        return str(output.stdout)
    

class FetchData:
    @kernel_function(description="Fetches data from the database and returns it as a string. Pass a duration in minutes to time limit the data. Pass a limit to control the number of entries to return.")
    def fetch_traffic(self, duration: int, limit: int) -> Annotated[str, "A string containing a list of traffic data entries."]:
        query = """
        MATCH (traffic:TRAFFIC)
        WHERE traffic.TIMESTAMP > datetime() - duration({minutes: $duration})
        RETURN DISTINCT
            traffic.IP_ADDRESS AS ip_address,
            traffic.PORT AS port,
            traffic.ORGANIZATION AS org,
            traffic.HOSTNAME AS hostname,
            traffic.LOCATION AS location,
            traffic.TOTAL_SIZE AS total_size,
            traffic.OUTLIER AS outlier,
            traffic.TIMESTAMP AS timestamp
        ORDER BY traffic.TIMESTAMP DESC
        LIMIT $limit
        """
        with driver.session(database=DATABASE) as session:
            CONSOLE.print(render_info_panel("DATA", "Fetching data from the database.", CONSOLE))
            result = session.run(query, duration=duration, limit=limit)
            data = []
            for record in result:
                data.append({
                    'ip_address': record['ip_address'],
                    'port': record['port'],
                    'org': record['org'],
                    'hostname': record['hostname'],
                    'location': record['location'],
                    'total_size': record['total_size'],
                    'outlier': record['outlier'],
                    'timestamp': record['timestamp']
                })
            CONSOLE.print(render_assistant_panel("DATA", pd.DataFrame(data).to_string(), CONSOLE))
            return str(data)
        

def send_email(content: str) -> bool:
    try:
        message = MIMEMultipart()
        message["From"] = EMAIL_SENDER
        message["To"] = EMAIL_RECIPIENT
        message["Subject"] = "📋 Situation Report"
        body = f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{content}\n\n** This is an automated report from the JAWS Network Monitoring System. **"""
        message.attach(MIMEText(body, "plain"))
        
        with smtplib.SMTP(EMAIL_SERVER, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(message)
        
        CONSOLE.print(render_success_panel("TOOL", f"Report emailed to: {EMAIL_RECIPIENT}", CONSOLE))
        return True
        
    except Exception as e:
        error_message = str(e)
        CONSOLE.print(render_error_panel("ERROR", error_message, CONSOLE))
        return False


class SendEmail:
    @kernel_function(description="Sends an email to High Command with the entire contents of the report.")
    def send_email(self, content: str) -> Annotated[str, "A system message once the process is complete."]:
        response = send_email(content)
        return str(response)