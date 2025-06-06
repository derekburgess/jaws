import subprocess
import pandas as pd
from smolagents import tool, CodeAgent, ToolCallingAgent, DuckDuckGoSearchTool, OpenAIServerModel #TransformersModel, HfApiModel
from jaws.jaws_config import *
from jaws.jaws_utils import dbms_connection

# Database not passed, uses the database set in jaws_config.py
driver = dbms_connection(DATABASE)


@tool
def list_interfaces() -> str:
    """
    Step 1: List available network interfaces.
    You will never want to select interfaces such as; 'lo', 'docker0', 'wlo1', etc.

    Returns:
        str: A string listing all available network interfaces.
    """ 
    interfaces = subprocess.run(['python', './jaws/jaws_capture.py', '--list', '--agent'], capture_output=True, text=True)
    return interfaces.stdout


@tool
def capture_packets(interface: str, duration: int) -> str:
    """
    Step 2: Captures packets into the database. 
    Choose a duration depending on the amount of data you want to capture. 
    Recommended not to exceed 60 seconds.

    Args:
        interface (str): The network interface to capture packets from.
        duration (int): The duration of the capture in seconds.

    Returns:
        str: The output of the jaws_capture command.
    """
    if duration > 60:
        duration = 60
    packets = subprocess.run(['python', './jaws/jaws_capture.py', '--interface', interface, '--duration', str(duration), '--agent'], capture_output=True, text=True)
    return packets.stdout


@tool
def document_organizations() -> str:
    """
    Step 3:Documents organizations by sending IP addresses to ipinfo.io.

    Returns:
        str: The output of the jaws_ipinfo command.
    """
    organizations = subprocess.run(['python', './jaws/jaws_ipinfo.py', '--agent'], capture_output=True, text=True)
    return organizations.stdout


@tool
def compute_embeddings() -> str:
    """
    Step 4: Processes network traffic data into embeddings for reporting and analysis.

    Returns:
        str: The output of the jaws_compute command.
    """
    embeddings = subprocess.run(['python', './jaws/jaws_compute.py', '--agent'], capture_output=True, text=True)
    return embeddings.stdout


@tool
def anomoly_detection() -> str:
    """
    Processes the network traffic data and embeddings into a set of plots for detecting anomalies. This includes:
    - A cluster plot for comparing packer sizes over ports.
    - A line plot showing the k-distances for determining the optimal epsilon value for DBSCAN.
    - A scatter plot of the PCA(Principal Component Analysis) reduced embeddings with outliers highlighted in red.

    Returns:
        str: The output from running the anomoly detection workflow.
    """
    output = subprocess.run(['python', './jaws/jaws_finder.py', '--agent'], capture_output=True, text=True)
    return output.stdout


@tool
def fetch_data() -> pd.DataFrame:
    """
    Fetches the latest data from the database and returns it as a DataFrame.
    Only returns the lastest 100 records created in the last 10 minutes.

    The DataFrame will have the following fields:
    - ip_address
    - port
    - org
    - hostname
    - location
    - total_size
    - outlier
    - timestamp
    
    Returns:
        pd.DataFrame: A DataFrame containing the latest data from the database.
    """
    query = """
    MATCH (traffic:TRAFFIC)
    WHERE traffic.TIMESTAMP > datetime() - duration({minutes: 10})
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
    LIMIT 100
    """
    with driver.session(database=DATABASE) as session:
        result = session.run(query)
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
        return pd.DataFrame(data)
    

network_analyst = ToolCallingAgent(
    name="network_analyst",
    description=ANALYST_SYSTEM_PROMPT,
    model=OpenAIServerModel(model_id=OPENAI_MODEL),
    #prompt_templates={"system_prompt": ANALYST_SYSTEM_PROMPT},
    #verbosity_level=0,
    tools=[list_interfaces, capture_packets, document_organizations, compute_embeddings, fetch_data]
)

expert_analysis = CodeAgent(
    name="network_security_advisor",
    description=ADVISOR_PROMPT,
    model=OpenAIServerModel(model_id=OPENAI_MODEL),
    planning_interval=1,
    max_steps=10,
    #verbosity_level=2,
    tools=[fetch_data, anomoly_detection, DuckDuckGoSearchTool()],
    additional_authorized_imports=["pandas"],
    managed_agents=[network_analyst]
)


def main():
    analysis = expert_analysis.run(ADVISOR_PROMPT)
    print(analysis)

if __name__ == "__main__":
    main()