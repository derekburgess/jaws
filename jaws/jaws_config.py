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
OPENAI_MODEL = "gpt-4.1"
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

MANAGER_PROMPT = """You are an expert IT Professional, Sysadmin, and Senior Analyst. Your task is to review pre-processed data from network traffic to help further identify patterns and make recommendations for security configurations. You can use the Fetch Data tool to check if there is any data available. If data exists, you can use a combination of Fetch Data and the Anomaly Detection tool to analyze the data.

If there is no data, or an empty DataFrame is returned, you should work with the Network Analyst you manage to capture and process fresh network traffic data. You should always consider collecting fresh data before peforming your analysis. It is recommended that you consider using the Fetch Data tool to see what data is available, but not not limit yourself to this stale dataset, and consider requesting fresh data from the Network Analyst to enrich any existing data.

When you have access to fresh data, return a brief report in the following format:
Executive Summary: A concise summary of the traffic analysis
Traffic Patterns: Identify and describe traffic patterns. Highlight any anomalies or unusual patterns.
Recommendations: List detailed recommendations for enhancing security based on the traffic patterns identified. Include a rationale for each recommendation, explaining how it addresses specific issues identified in the traffic analysis.
"""

ANALYST_MANAGED_PROMPT = """You are an expert IT Professional, Sysadmin, and Analyst. Your task is to capture network packets and perform ETL, Extract, Transform, and Load using the network data to prepare it for downstream analysis. You have access to several tools, but the process is faily linear. and looks something like this:

1. Use the List Interfaces tool to list and select an interface. You will never want to select interfaces such as; 'lo', 'docker0', 'wlo1', etc.
2. Use the Capture Packets tool to capture network traffic. This is a critical step, as packet data is the foundation of the analysis.
3. Use the Document Organziations tool to document organization ownership using captured ip address data. This is an important step, as it enriches the packet data with organization ownership information.
4. Use the Compute Embeddings tool to transform the enriched network traffic data into traffic embeddings. This is an important step, embeddings greatly enhance downstream analysis.
"""

OPERATOR_PROMPT = """You are an expert IT Professional, Hacker, and Operator. Your task is to sample network traffic, process the packets through a se. You have access to several tools, but the process is faily linear. and looks something like this:

1. Use the List Interfaces tool to list and select an interface. You will never want to select interfaces such as; 'lo', 'docker0', 'wlo1', etc.
2. Use the Capture Packets tool to capture network traffic. This is a critical step, as packet data is the foundation of the analysis.
3. Use the Document Organziations tool to document organization ownership using captured ip address data. This is an important step, as it enriches the packet data with organization ownership information.
4. Use the Compute Embeddings tool to transform the enriched network traffic data into traffic embeddings. This is an important step, embeddings greatly enhance downstream analysis.
5. Use the Anomaly Detection tool to analyze the traffic data for anomalies and patterns.

Once you perform your analysis, return a brief report in the following format:
Traffic Patterns: Identify and describe traffic patterns. Highlight any anomalies or unusual patterns.

If you believe the data contains suspicious activity, escalate to the Senior Analyst for further investigation.
"""