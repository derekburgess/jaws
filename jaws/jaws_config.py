import os
from openai import OpenAI
from neo4j import GraphDatabase
from transformers import BitsAndBytesConfig


# Graph database configuration.
DATABASE = "captures" # Created using the Neo4j Desktop app. Default is 'captures'.
NEO4J_URI = os.getenv("NEO4J_URI") # See README.md
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") # See README.md
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD") # See README.md
NEO4J = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

IPINFO_API_KEY = os.getenv("IPINFO_API_KEY")

CLIENT = OpenAI()
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENAI_LANG_MODEL = "gpt-4.1"

# The packet model is used to process packet strings into embeddings.
PACKET_MODEL = "bigcode/starcoder2-3b"
PACKET_MODEL_ID = "starcoder"
QUANTIZATION_CONFIG = BitsAndBytesConfig(load_in_8bit=True)

# The language model is used for language related tasks, such as analyzing data, or generating reports.
LANG_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"
LANG_MODEL_ID = "llama"

# Not in use yet.
# The reasoning model is used to support the language model.
REASONING_MODEL = "o4-mini"
REASONING_MODEL_ID = "o4-mini"

# Saves plots to this location.
FINDER_ENDPOINT = os.getenv("JAWS_FINDER_ENDPOINT")

# Advisor system prompt
ADVISOR_SYSTEM_PROMPT = rf"""
You are an expert IT Professional, Sysadmin, and Analyst. Your task is to review data from network traffic to identify patterns and make recommendations for firewall configurations. Please analyze the provided network traffic and cluster plot, then return a brief report in the following format:
---
Executive Summary:
A concise summary of the traffic analysis, including a description of the cluster plot.

Traffic Analysis:
1. Common Traffic Patterns: Identify and describe the regular traffic patterns. Highlight any anomalies or unusual patterns.
2. Network Diagram: Create an ASCII-based diagram that illustrates the network. Include organizations, hostnames, IP addresses, port numbers, and traffic size.

Example of network diagram to follow:

[External]   [External]   [External]
 (IP:Port)    (IP:Port)    (IP:Port)
    \            |            /
     \           |           /
       -----[WAN Router]----
                 |
             [Firewall]
                 |
       -----[LAN Switch]----
      /          |          \
     /           |           \
[Internal]   [Internal]   [Internal]
 (IP:Port)    (IP:Port)    (IP:Port)

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
