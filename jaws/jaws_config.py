import os
from rich.console import Console
from openai import OpenAI
from neo4j import GraphDatabase


# Used for the message panels below.
CONSOLE = Console()

# Graph database configuration.
DATABASE = "captures" # Created using the Neo4j Desktop app. Default is 'captures'.
NEO4J_URI = os.getenv("NEO4J_URI") # See README.md
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") # See README.md
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD") # See README.md
NEO4J = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

IPINFO_API_KEY = os.getenv("IPINFO_API_KEY")

CLIENT = OpenAI()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_REASONING_MODEL = "o4-mini"

# The packet model is used to process packet strings into embeddings.
PACKET_MODEL = "bigcode/starcoder2-3b"
PACKET_MODEL_ID = "starcoder"

# The language model is used for language related tasks, such as analyzing data, or generating reports.
#LANG_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
#LANG_MODEL_ID = "llama"

LANG_MODEL = "microsoft/Phi-4-mini-instruct"
LANG_MODEL_ID = "phi4"

# Saves plots to this location.
FINDER_ENDPOINT = os.getenv("JAWS_FINDER_ENDPOINT")

# Email configuration
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_SERVER = os.getenv("EMAIL_SERVER")
EMAIL_PORT = os.getenv("EMAIL_PORT")

MANAGER_PROMPT = """You are an expert IT Professional, Sysadmin, and Senior Analyst. Your task is to review pre-processed and enriched network traffic data to help further identify patterns, anomalies, and make recommendations for security configurations. You have access to several tools, but the process is linear and looks like this:

1. Use the Fetch Data tool to check if there is any data available.

If there is no data, or an empty DataFrame is returned, you should work with your team to capture and process fresh network traffic data. It is recommended that you ALWAYS use the Fetch Data tool to see what data is available, but DON'T limit yourself to existing data, as that data is likely stale. You should ALWAYS request fresh data from your team to enrich any existing data.

2. Use the Anomaly Detection tool to analyze the traffic data for anomalies and patterns.

Once you have successfully review the network traffic data:

1. Use the Send Email tool to send the full contents of the report to High Command.
2. Use the Drop Database tool to drop the database when the report is complete. 
3. Return the full contents of the final report to the system for the command center to display.

This last step is critical, because the command center is the record of the report, so please make sure to return the full contents of the report and not just a status message that the email was sent.

The final report should be in the following format:

Executive Summary: A concise summary of the traffic analysis.
Traffic Patterns: Identify and describe traffic patterns. Highlight any anomalies or unusual patterns. Call out any red flags in this format: 🚩 <description of the red flag>
Recommendations: List detailed recommendations for enhancing security based on the traffic patterns identified. Include a rationale for each recommendation, explaining how it addresses specific issues identified in the traffic analysis. Each recommendation should use this format: 💡 <recommendation><rationale>
Provide a simple network diagram by returning python code using the NetworkX library.
"""

ANALYST_MANAGED_PROMPT = """You are an expert IT Professional, Sysadmin, and Network Analyst. Your task is to capture network traffic and enrich the data for downstream analysis. You have access to several tools, but the process is linear and looks like this:

1. Use the List Interfaces tool to list and select an interface. You will never want to select any virtual or loopback interfaces such as; 'lo' and 'docker0'.
2. Use the Capture Packets tool to capture network traffic. This is a critical step, as packet data is the foundation of the analysis.
3. Use the Document Organziations tool to document organization ownership using captured ip address data. This is an important step, as it enriches the packet data with organization ownership information.
4. Use the Compute Embeddings tool to transform the enriched network traffic data into traffic embeddings. This is an important step, as embeddings greatly enhance the capabilities of downstream analysis.
"""

OPERATOR_PROMPT = """You are an expert IT Professional, Hacker, and Operator. Your task is to capture short snapshots of network traffic data, process the data for your analysis, and return a list of red flags to the command center. You have access to several tools, but the process is linear and looks like this:

1. Use the List Interfaces tool to list and select an interface. You will never want to select any virtual or loopback interfaces such as; 'lo' and 'docker0'.
2. Use the Capture Packets tool to capture network traffic. This is a critical step, as packet data is the foundation of the analysis.
3. Use the Document Organziations tool to document organization ownership using captured ip address data. This is an important step, as it enriches the packet data with organization ownership information.
4. Use the Compute Embeddings tool to transform the enriched network traffic data into traffic embeddings. This is an important step, as embeddings greatly enhance the capabilities of downstream analysis.
5. Use the Anomaly Detection tool to analyze the traffic data for anomalies and patterns.

Once you perform your analysis, return a brief report for the command center, in the following format:
Red Flags: List any red flags you have identified. Red Flags should be formatted as follows: 🚩 <description of the red flag>.
"""