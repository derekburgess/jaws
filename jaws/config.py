import os
import sys
from functools import lru_cache
from rich.console import Console
from openai import OpenAI
from neo4j import GraphDatabase


# Used for the message panels below.
CONSOLE = Console()

# Raw (non-rich) output mode. Auto-enabled when stdout is not a TTY — which is
# exactly the case when a script is run as a subprocess with captured output by
# the MCP server. Humans running a script directly in a terminal get the pretty
# rich panels; the MCP server gets clean, parseable text. Detected automatically,
# so callers never have to opt in.
AGENT_MODE = not sys.stdout.isatty()

# Graph database configuration. The URI and username fall back to the standard
# local-install values (see README.md) because the process env is not guaranteed to
# carry them: MCP clients spawn the server as a child process and may strip the
# environment (the Python MCP SDK whitelists only PATH/HOME/etc., and GUI-launched
# clients never see shell exports at all). The password has no safe default and must
# arrive via the environment — jaws_mcp/mcp-local.json shows how to pass it through.
DATABASE = "captures" # Created using the Neo4j Desktop app. Default is 'captures'.
NEO4J_URI = os.getenv("NEO4J_URI") or "bolt://localhost:7687"
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME") or "neo4j"
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")


# The OpenAI client and Neo4j driver are created lazily so that importing this
# module never reaches out for credentials. The local (transformers) path needs
# neither OpenAI nor — for model downloads — Neo4j, so eager construction would
# break those flows when OPENAI_API_KEY / NEO4J_URI are unset. lru_cache makes
# each a process-wide singleton, matching the previous module-level behavior.
@lru_cache(maxsize=1)
def get_neo4j_driver():
    # Fail with the actual problem instead of the driver's "URI scheme b''" — this
    # message is what surfaces in the MCP error envelope when credentials never
    # reached the server process.
    if not NEO4J_PASSWORD:
        raise ValueError(
            "NEO4J_PASSWORD is not set. Export it in the environment, or pass it "
            "through your MCP client's env block (see jaws_mcp/mcp-local.json)."
        )
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


@lru_cache(maxsize=1)
def get_openai_client():
    return OpenAI()


IPINFO_API_KEY = os.getenv("IPINFO_API_KEY")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"

# Local embedding models, selectable by short id (jaws-compute --model <id>). They run
# fully on-device via sentence-transformers, which reads each model's own pooling and
# normalization config — so adding a model needs no new code, just an id -> HF name entry.
PACKET_MODELS = {
    "jina-code": "jinaai/jina-embeddings-v2-base-code",
    # Cisco's security-domain bi-encoder (ModernBERT, 768-dim, sentence-transformers
    # native). An alternative to jina-code's code-token specialization: tests whether a
    # cybersecurity-trained embedder clusters endpoints better. Both are 768-dim, so the
    # downstream PCA/DBSCAN path is unchanged. Predownload via `jaws-utils --model securebert`.
    "securebert": "cisco-ai/SecureBERT2.0-biencoder",
    # Add more here, e.g.:
    # "bge-small": "BAAI/bge-small-en-v1.5",
    # "nomic": "nomic-ai/nomic-embed-text-v1.5",
    # "gte-base": "thenlper/gte-base",
}
DEFAULT_PACKET_MODEL = "jina-code"

# Saves plots to this location.
FINDER_ENDPOINT = os.getenv("JAWS_FINDER_ENDPOINT")
