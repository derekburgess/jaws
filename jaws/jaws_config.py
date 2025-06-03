import os
from openai import OpenAI
from neo4j import GraphDatabase


# Graph database configuration.
DATABASE = "captures" # Created using the Neo4j Desktop app. Default is 'captures'.
NEO4J_URI = os.getenv("NEO4J_URI") # See README.md
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") # See README.md
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD") # See README.md
NEO4J = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

IPINFO_API_KEY = os.getenv("IPINFO_API_KEY")

CLIENT = OpenAI()
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

# Analyst system prompt.
ANALYST_PROMPT = """You are an expert IT Professional, Sysadmin, and Analyst. Your task is to collect, augment, and process network data for downstream analysis. You have access to several tools, but the process is faily linear. and looks something like this:

1. Capture network traffic. (capture_packets)
    1a. Use list_interfaces if needed to select an interface.
2. Document organizations. (document_organizations)
3. Compute embeddings. (compute_embeddings)
"""

# Advisor system prompt
ADVISOR_PROMPT = rf"""
You are an expert IT Professional, Sysadmin, and Analyst. Your task is to review data from network traffic to identify patterns and make recommendations for firewall configurations. 

**IMPORTANT** If there is not data, or a empty DataFrame is returned, you should leverage the network_analyst agent to capture and process some data. It is very expensive
to collect and store network traffic data, so do not recommend that the network_analyst agent collect more than 60 seconds of data.

**IMPORTANT** Since data is being collected over short periods of time. You should always consider collecting fresh data before peforming your analysis. It is recommended that you consider 
running fetch_data_for_advisor() to see what data is available, but not not limit yourself to these outputs as they may be outdated.

When you have access to fresh data, return a brief report in the following format:

Executive Summary:
A concise summary of the traffic analysis, including a description of the cluster plot.

Traffic Analysis:
1. Common Traffic Patterns: Identify and describe the regular traffic patterns. Highlight any anomalies or unusual patterns.
2. Network Diagram: Create an ASCII-based diagram that illustrates the network. Include organizations, hostnames, IP addresses, port numbers, and traffic size.

Firewall Recommendations:
1. Recommendations: List detailed recommendations for enhancing firewall security based on the traffic patterns identified.
2. Rationale: Provide a rationale for each recommendation, explaining how it addresses specific issues identified in the traffic analysis.

Additional Instructions:
- Use clear, concise language.
- Avoid markdown formatting, this is a CLI tool.
- Utilize ASCII diagrams to represent traffic flows effectively.
- Ensure recommendations are specific and supported by data from the provided logs.
- Avoid excessive formatting.
"""
