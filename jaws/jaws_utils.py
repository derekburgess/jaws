import argparse
import json
import sys
from contextlib import contextmanager
from rich.text import Text
from rich.panel import Panel
from rich.live import Live
from sentence_transformers import SentenceTransformer
from jaws.config import (
    CONSOLE,
    AGENT_MODE,
    DATABASE,
    PACKET_MODELS,
    get_neo4j_driver,
)


# Utility functions imported elsewhere.
def render_error_panel(title, message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"{title}", border_style="red", width=width)


def render_info_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"{title}", border_style="yellow", width=width)


def render_success_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"{title}", border_style="green", width=width)


def render_input_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="left"), title=f"{title}", border_style="blue", width=width)


def render_assistant_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="left"), title=f"{title}", border_style="yellow", width=width)


def render_response_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="left"), title=f"{title}", border_style="green", width=width)


def render_activity_panel(title, recent_packets, console, height=10):
    width = console.size.width
    lines = recent_packets[-(height-2):]
    while len(lines) < (height-2):
        lines.insert(0, "")
    text = "\n".join(lines)
    return Panel(Text(text, justify="center"), title=f"{title}", border_style="blue", width=width, height=height)


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
        # Human panel; structured error on stdout for agents.
        if self.agent:
            print(json.dumps({"error": message}))
        else:
            CONSOLE.print(render_error_panel(title, message, CONSOLE))

    def result(self, obj, summary=None):
        # The machine surface: one structured JSON object on stdout in agent mode;
        # a human summary panel in pretty mode (the detail already scrolled by).
        if self.agent:
            print(json.dumps(obj, default=str, indent=2))
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
        SentenceTransformer(model, trust_remote_code=True)
        reporter.result({"downloaded": model}, summary=f"Downloaded: {model}")
    except Exception as e:
        reporter.error("ERROR", f"{model}\n\n{str(e)}")


# Support function, imported heavily throughtout the project.
def dbms_connection(database, reporter=None):
    reporter = reporter or Reporter()
    try:
        driver = get_neo4j_driver()
        with driver.session(database=database) as session:
            session.run("RETURN 1")
        return driver
    except Exception as e:
        message = str(e)
        if "database does not exist" in message.lower():
            reporter.error("ERROR", f"'{database}' database does not exist.\nYou need to create the default '{DATABASE}' database or provide the name of an existing database.")
        else:
            reporter.error("ERROR", f"Could not connect to Neo4j (check NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD and that the database is running).\n{message}")
        return None

# Populate database with schema. Called prior to capture.
def initialize_schema(driver, database, local_ip, reporter):
    schema_definitions = [
        {
            "type": "constraint",
            "name": "ip_address_unique",
            "label": "IP_ADDRESS",
            "properties": ["IP_ADDRESS"],
            "query": "CREATE CONSTRAINT ip_address_unique IF NOT EXISTS FOR (ip:IP_ADDRESS) REQUIRE ip.IP_ADDRESS IS UNIQUE"
        },
        {
            "type": "index",
            "name": "packet_timestamp_index", 
            "label": "PACKET",
            "properties": ["TIMESTAMP"],
            "query": "CREATE INDEX packet_timestamp_index IF NOT EXISTS FOR (p:PACKET) ON (p.TIMESTAMP)"
        },
        {
            "type": "index",
            "name": "port_composite_index",
            "label": "PORT",
            "properties": ["PORT", "IP_ADDRESS"],
            "query": "CREATE INDEX port_composite_index IF NOT EXISTS FOR (p:PORT) ON (p.PORT, p.IP_ADDRESS)"
        },
        {
            "type": "constraint",
            "name": "organization_unique",
            "label": "ORGANIZATION",
            "properties": ["ORGANIZATION"],
            "query": "CREATE CONSTRAINT organization_unique IF NOT EXISTS FOR (org:ORGANIZATION) REQUIRE org.ORGANIZATION IS UNIQUE"
        },
        {
            "type": "home_organization",
            "name": "YOU ARE HERE",
            "description": "Create an organization for the current system's IP address.",
            "query": "MERGE (ip:IP_ADDRESS {IP_ADDRESS: $local_ip}) MERGE (org:ORGANIZATION {ORGANIZATION: 'YOU ARE HERE'}) MERGE (org)-[:OWNERSHIP]->(ip)",
            "parameters": {"local_ip": local_ip}
        },
        {
            "type": "index",
            "name": "endpoint_ip_index",
            "label": "ENDPOINT",
            "properties": ["IP_ADDRESS"],
            "query": "CREATE INDEX endpoint_ip_index IF NOT EXISTS FOR (e:ENDPOINT) ON (e.IP_ADDRESS)"
        }
    ]
    
    with driver.session(database=database) as session:
        errors = []
        for schema in schema_definitions:
            try:
                session.run(schema["query"], schema.get("parameters", {}))
            except Exception as e:
                errors.append(str(e))
        
        if errors:
            details = "\n".join(f"  - {error}" for error in errors)
            reporter.info("WARNING", f"Schema initialization for '{database}' encountered {len(errors)} error(s):\n{details}")
        else:
            reporter.info("CONFIG", f"Schema ready for: '{database}'")


# Drops all entities from the database.
def drop_database(driver, database, reporter):
    with driver.session(database=database) as session:
        count_result = session.run("MATCH (n) RETURN count(n)")
        count = count_result.single()[0]
        if count == 0:
            return reporter.result({"database": database, "dropped": 0, "empty": True}, summary=f"'{database}' is empty.")
        if not reporter.agent:
            reporter.info("WARNING", f"This will permanently delete all {count} entities from '{database}'.\nType the database name '{database}' to confirm.")
            confirmation = input(f"Type '{database}' to confirm: ")
            if confirmation.strip() != database:
                return reporter.info("CANCELLED", f"Drop cancelled. '{database}' was not modified.")
        session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
        return reporter.result({"database": database, "dropped": count}, summary=f"Dropped({count}): '{database}'")


def main():
    parser = argparse.ArgumentParser(description="Utility functions for JAWS | 1.) Download models 2.) Drop database")
    parser.add_argument("--drop", default=DATABASE, help=f"Specify a database to drop (default: '{DATABASE}').")
    parser.add_argument("--model", choices=list(PACKET_MODELS), help="Specify a model id to download (see config.PACKET_MODELS).")
    args = parser.parse_args()
    reporter = Reporter()

    if args.model:
        download_model(PACKET_MODELS[args.model], reporter)
        return

    driver = dbms_connection(args.drop, reporter)
    if driver is None:
        return
    drop_database(driver, args.drop, reporter)
    driver.close()

if __name__ == "__main__":
    main()