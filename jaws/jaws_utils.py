import argparse
import json
import sys
from contextlib import contextmanager

from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from jaws.config import (
    AGENT_MODE,
    CONSOLE,
    DATABASE,
    PACKET_MODELS,
    get_neo4j_driver,
)
from jaws.domain import (
    NON_CONVERSATIONAL_CLASSIFICATIONS,
    classify_ip_address,
    legacy_failure,
    legacy_success,
)
from jaws.optional_dependencies import require_module
from jaws.storage import Neo4jDatabaseRuntime
from jaws.storage.migrations import MigrationError
from jaws.storage.migrations import manager as migration_manager

# Address-scope classification shared by compute (tags each profile), finder (excludes
# non-conversational endpoints from the rankings), and ipinfo (skips lookups that can
# only return bogons). Multicast/broadcast destinations never reply, so directional
# ratios computed against them (out/in, upload/download) are structurally one-sided —
# without this, SSDP/mDNS chatter tops both anomaly rankings looking "exfil-shaped".
NON_CONVERSATIONAL_TYPES = NON_CONVERSATIONAL_CLASSIFICATIONS


def classify_endpoint(ip_string):
    """Coarse endpoint_type for an IP, via stdlib ipaddress.

    Returns one of: 'multicast', 'broadcast', 'unspecified', 'loopback', 'link-local',
    'private', 'public', 'reserved', or 'unknown' (unparseable). Types in
    NON_CONVERSATIONAL_TYPES are one-way by construction and are kept out of anomaly
    rankings; 'public' is the only type worth an ipinfo lookup. Order matters below:
    loopback/link-local addresses are also is_private, so they are checked first.
    """
    return classify_ip_address(ip_string)


# Minimum packets in a single-direction stream before its timing is meaningful.
# Six packets give five inter-packet intervals — enough for a coefficient of variation
# that reflects cadence rather than a single burst. Below this in BOTH directions,
# timing is left undefined (None) at compute time and the finder imputes it, so sparse
# endpoints aren't mistaken for perfectly regular beacons (the old gate of 3 let a lone
# handshake read as interval_cv ≈ 0.33, i.e. "beacon-like"). Lives here so
# jaws_compute (the gate) and jaws_finder (suppression for graphs computed under the
# old gate) share one value.
MIN_TIMING_PACKETS = 6


# Utility functions imported elsewhere.
def render_error_panel(title, message, console):
    width = console.size.width
    return Panel(
        Text(message, justify="left"),
        title=f"{title}",
        title_align="left",
        border_style="red",
        width=width,
    )


def render_info_panel(title, message, console):
    width = console.size.width
    return Panel(
        Text(message, justify="left"),
        title=f"{title}",
        title_align="left",
        border_style="yellow",
        width=width,
    )


def render_success_panel(title, message, console):
    width = console.size.width
    return Panel(
        Text(message, justify="left"),
        title=f"{title}",
        title_align="left",
        border_style="green",
        width=width,
    )


def render_activity_panel(title, recent_packets, console, height=10):
    width = console.size.width
    lines = recent_packets[-(height - 2) :]
    while len(lines) < (height - 2):
        lines.insert(0, "")
    text = "\n".join(lines)
    return Panel(
        Text(text, justify="left"),
        title=f"{title}",
        title_align="left",
        border_style="cornflower_blue",
        width=width,
        height=height,
    )


# Single output abstraction with two distinct surfaces:
#   - pretty mode (interactive TTY): rich panels for a human.
#   - agent mode (piped, e.g. the MCP server): a single structured JSON object on
#     stdout via result(); progress narration (info/success) is routed to stderr so
#     it never pollutes that machine-readable result, and errors return structured JSON.
# Defaults to AGENT_MODE, auto-detected from whether stdout is a TTY.
class Reporter:
    def __init__(self, agent=AGENT_MODE):
        self.agent = agent

    def info(self, title, message):
        # Progress narration. In agent mode it goes to stderr — kept for debugging
        # (and surfaced by the MCP only on process failure), never on the result.
        if self.agent:
            print(f"[{title}] {message}", file=sys.stderr)
        else:
            CONSOLE.print(render_info_panel(title, message, CONSOLE))

    def success(self, title, message):
        # Non-terminal completion narration. Terminal output should use result().
        if self.agent:
            print(f"[{title}] {message}", file=sys.stderr)
        else:
            CONSOLE.print(render_success_panel(title, message, CONSOLE))

    def error(self, title, message):
        # Human panel; structured error on stdout for agents. Every agent-mode
        # payload carries a boolean `ok` so callers branch on a field, not on the
        # presence of an "error" key — the failure half of the {"ok": ...} envelope.
        if self.agent:
            print(json.dumps(legacy_failure(message)))
        else:
            CONSOLE.print(render_error_panel(title, message, CONSOLE))

    def result(self, obj, summary=None):
        # The machine surface: one structured JSON object on stdout in agent mode;
        # a human summary panel in pretty mode (the detail already scrolled by).
        # The success half of the envelope: ok=True merged with the result fields
        # (obj is always a dict and never carries its own "ok").
        if self.agent:
            print(json.dumps(legacy_success(obj), default=str, indent=2))
        elif summary is not None:
            CONSOLE.print(render_success_panel("PROCESS COMPLETE", summary, CONSOLE))

    def raw(self, text):
        # Pretty-mode plain output (plotille charts); callers guard it to non-agent mode.
        print(text)

    @contextmanager
    def activity(self, render):
        """Drive a live-updating panel group while iterating (pretty mode only).

        Yields an `update()` callable to invoke after each item. In agent mode it is
        a no-op — per-item detail belongs in the final structured result(), not streamed
        onto the machine surface.
        """
        if self.agent:
            yield lambda *a, **k: None
        else:
            with Live(render(), console=CONSOLE, refresh_per_second=10) as live:
                yield lambda *a, **k: live.update(render())


# Downloads the models to the local device.
# Nice if you do not want to wait for model downloads on first compute.
def download_model(model, reporter):
    try:
        reporter.info("INFO", f"Downloading: {model}")
        sentence_transformers = require_module(
            "sentence_transformers", "local-embeddings", "Local model downloads"
        )
        sentence_transformers.SentenceTransformer(model, trust_remote_code=True)
        reporter.result({"downloaded": model}, summary=f"Downloaded: {model}")
    except Exception as e:
        reporter.error("ERROR", f"{model}\n\n{str(e)}")


# Support function, imported heavily throughtout the project.
def dbms_connection(database, reporter=None):
    reporter = reporter or Reporter()
    try:
        driver = get_neo4j_driver()
        Neo4jDatabaseRuntime(driver, database).probe()
        return driver
    except Exception as e:
        message = str(e)
        if "database does not exist" in message.lower():
            reporter.error(
                "ERROR",
                f"'{database}' database does not exist.\nYou need to create the default '{DATABASE}' database or provide the name of an existing database.",
            )
        else:
            reporter.error(
                "ERROR",
                f"Could not connect to Neo4j (check NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD and that the database is running).\n{message}",
            )
        return None


# Populate database with schema. Called prior to capture.
def initialize_schema(driver, database, local_ip, reporter):
    try:
        result = migration_manager(driver, database).migrate()
        Neo4jDatabaseRuntime(driver, database).ensure_local_perspective(local_ip)
        reporter.info(
            "CONFIG",
            f"Schema ready for: '{database}' (version {result.status.current_version})",
        )
    except Exception as error:
        reporter.info(
            "WARNING",
            f"Schema migration for '{database}' failed; capture will not start:\n  - {error}",
        )
        raise MigrationError(f"schema migration failed for '{database}'") from error


def main():
    parser = argparse.ArgumentParser(description="Download optional local embedding models.")
    parser.add_argument(
        "--drop",
        metavar="DATABASE",
        help="Removed: use the human-only guarded `jaws-admin plan/erase` workflow.",
    )
    parser.add_argument(
        "--model",
        choices=list(PACKET_MODELS),
        help="Specify a model id to download (see config.PACKET_MODELS).",
    )
    args = parser.parse_args()
    reporter = Reporter()

    if args.drop:
        parser.error(
            "--drop was removed; run `jaws-admin plan --database NAME`, then repeat its exact "
            "confirmation with `jaws-admin erase --database NAME --confirm ...`"
        )

    if args.model:
        download_model(PACKET_MODELS[args.model], reporter)
        return

    parser.error("one of --model is required")


if __name__ == "__main__":
    main()
