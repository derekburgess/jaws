"""Process-wide settings instance and the legacy module-level names built from it.

`jaws.settings` owns validation and category separation. This module holds the one
process-wide `SETTINGS` and re-exports the flat names the CLI, MCP server, and tests
already import, so existing installations and call sites keep working while the service
ports land. New code should take a `Settings` argument or read `SETTINGS`, not these
aliases; the aliases are removed when the Milestone 3 CLI adapters take settings directly.
"""

from functools import lru_cache

from rich.console import Console

from jaws.optional_dependencies import require_module
from jaws.settings import Settings, load_settings

# Used for the message panels below.
CONSOLE = Console()

SETTINGS: Settings = load_settings()

# Raw (non-rich) output mode, auto-detected from stdout. See Settings/_detect_agent_mode.
AGENT_MODE = SETTINGS.interface.agent_mode

# Graph database configuration. The URI and username fall back to the standard
# local-install values (see README.md) because the process env is not guaranteed to
# carry them; the password has no safe default and must arrive via the environment —
# jaws_mcp/mcp-local.json shows how to pass it through.
DATABASE = SETTINGS.database.name  # Created using the Neo4j Desktop app. Default is 'captures'.
NEO4J_URI = SETTINGS.database.uri
NEO4J_USERNAME = SETTINGS.database.username
NEO4J_PASSWORD = SETTINGS.database.password.reveal()


# The OpenAI client and Neo4j driver are created lazily so that importing this
# module never reaches out for credentials. The local (transformers) path needs
# neither OpenAI nor — for model downloads — Neo4j, so eager construction would
# break those flows when OPENAI_API_KEY / NEO4J_URI are unset. lru_cache makes
# each a process-wide singleton, matching the previous module-level behavior.
@lru_cache(maxsize=1)
def get_neo4j_driver():
    # Fail with the actual problem instead of the driver's "URI scheme b''" — this
    # message is what surfaces in the MCP error envelope when credentials never
    # reached the server process. SettingsError is a ValueError, so callers that
    # already catch ValueError here are unaffected.
    password = SETTINGS.database.require_password()
    neo4j = require_module("neo4j", "neo4j", "Neo4j storage")
    return neo4j.GraphDatabase.driver(
        SETTINGS.database.uri, auth=(SETTINGS.database.username, password)
    )


@lru_cache(maxsize=1)
def get_openai_client():
    api_key = SETTINGS.provider.require_openai_api_key()
    openai = require_module("openai", "openai-embeddings", "OpenAI embeddings")
    return openai.OpenAI(api_key=api_key)


IPINFO_API_KEY = SETTINGS.provider.ipinfo_api_key.reveal()


def get_ipinfo_api_key():
    """Return the configured IPinfo key, validating it only when enrichment runs."""
    return SETTINGS.provider.require_ipinfo_api_key()


# ASNs that primarily host or front OTHER organizations' workloads (IaaS, CDN, reverse
# proxies). Ipinfo labels an IP with its ASN's owner, so on these networks the org
# string names the infrastructure provider, not the actual counterparty — "AS396982
# Google LLC" is some GCP customer's VM, not Google (Google's own services ride
# AS15169). Triage surfaces attach `cloud_hosted` from this set so the provider's name
# isn't read as the service's reputation — attacker infrastructure lives in exactly
# these networks. Curated, not exhaustive: extend as new hosting ASNs show up.
#
# This is provider-independent reference data rather than configuration; it moves to the
# Milestone 3 enrichment service, which owns deterministic classification.
HOSTING_ASNS = {
    "AS396982",  # Google Cloud Platform (customer VMs)
    "AS16509",
    "AS14618",  # Amazon AWS / EC2
    "AS8075",  # Microsoft (Azure customers share it with Microsoft's own services)
    "AS13335",  # Cloudflare (reverse proxy — the origin is hidden behind it)
    "AS54113",  # Fastly (CDN)
    "AS16625",
    "AS20940",  # Akamai (CDN)
    "AS14061",  # DigitalOcean
    "AS16276",  # OVH
    "AS24940",  # Hetzner
    "AS63949",  # Linode (Akamai)
    "AS20473",  # Vultr
    "AS31898",  # Oracle Cloud
    "AS45102",  # Alibaba Cloud
}


def is_cloud_hosted(org):
    """True when an org label's leading ASN is a hosting/CDN provider (HOSTING_ASNS)."""
    parts = org.split() if org else []
    return bool(parts) and parts[0] in HOSTING_ASNS


OPENAI_API_KEY = SETTINGS.provider.openai_api_key.reveal()
OPENAI_EMBEDDING_MODEL = SETTINGS.model.openai_embedding_model

# Local embedding models, selectable by short id (jaws-compute --model <id>). Defined in
# jaws.settings; adding a model needs no new code, just an id -> HF name entry there.
PACKET_MODELS = dict(SETTINGS.model.packet_models)
DEFAULT_PACKET_MODEL = SETTINGS.model.default_packet_model

# Saves plots to this location.
FINDER_ENDPOINT = SETTINGS.artifacts.finder_endpoint
