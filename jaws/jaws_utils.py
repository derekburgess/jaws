import argparse
from contextlib import contextmanager
from rich.text import Text
from rich.panel import Panel
from rich.live import Live
from transformers import AutoTokenizer, AutoModel
from jaws.config import (
    CONSOLE,
    AGENT_MODE,
    DATABASE,
    PACKET_MODEL,
    PACKET_MODEL_ID,
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


# Single output abstraction. Each script builds one Reporter and calls its
# methods instead of branching on the output mode at every call site. In pretty
# mode (interactive TTY) it renders rich panels; in raw mode (piped, e.g. the
# MCP server) it emits plain, parseable `[TITLE] message` lines to stdout.
# Defaults to AGENT_MODE, which is auto-detected from whether stdout is a TTY.
class Reporter:
    def __init__(self, agent=AGENT_MODE):
        self.agent = agent

    def info(self, title, message):
        if self.agent:
            print(f"[{title}] {message}")
        else:
            CONSOLE.print(render_info_panel(title, message, CONSOLE))

    def success(self, title, message):
        if self.agent:
            print(f"[{title}] {message}")
        else:
            CONSOLE.print(render_success_panel(title, message, CONSOLE))

    def error(self, title, message):
        if self.agent:
            print(f"[{title}] {message}")
        else:
            CONSOLE.print(render_error_panel(title, message, CONSOLE))

    def raw(self, text):
        # Mode-independent plain output (interface lists, formatted reports,
        # plotille charts) that agents parse and humans also want to see.
        print(text)

    @contextmanager
    def activity(self, render):
        """Stream content while iterating, styled or plain.

        `render` is a zero-arg callable returning the renderable to display.
        Yields an `update(line=None)` callable to invoke after each item:
          - pretty mode drives a rich.Live (the rolling panel view);
          - agent mode prints `line` plainly when given, so the agent sees the
            same per-item content the human watches scroll by, minus borders.
        Pass no `line` (capture/compute) to keep agent output to the final
        summary instead of streaming a high-volume firehose.
        """
        if self.agent:
            yield lambda line=None: print(line) if line is not None else None
        else:
            with Live(render(), console=CONSOLE, refresh_per_second=10) as live:
                yield lambda line=None: live.update(render())


# Downloads the models to the local device.
# Nice if you do not want to wait for model downloads on first compute.
def download_model(model, reporter):
    try:
        reporter.info("INFO", f"Downloading: {model}")
        AutoTokenizer.from_pretrained(model, trust_remote_code=True)
        AutoModel.from_pretrained(model, trust_remote_code=True)
        reporter.success("PROCESS COMPLETE", f"Downloaded: {model}")
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
            reporter.error("ERROR", f"Schema initialization for '{database}' encountered {len(errors)} error(s):\n{details}")
        else:
            reporter.success("PROCESS COMPLETE", f"Schema has been initialized for: '{database}'")


# Drops all entities from the database.
def drop_database(driver, database, reporter):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        if count == 0:
            return reporter.info("INFO", f"'{database}' is empty.")
        if not reporter.agent:
            reporter.info("WARNING", f"This will permanently delete all {count} entities from '{database}'.\nType the database name '{database}' to confirm.")
            confirmation = input(f"Type '{database}' to confirm: ")
            if confirmation.strip() != database:
                return reporter.info("CANCELLED", f"Drop cancelled. '{database}' was not modified.")
        session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
        return reporter.success("PROCESS COMPLETE", f"Dropped({count}): '{database}'")


def main():
    parser = argparse.ArgumentParser(description="Utility functions for JAWS | 1.) Download models 2.) Drop database")
    parser.add_argument("--drop", default=DATABASE, help=f"Specify a database to drop (default: '{DATABASE}').")
    parser.add_argument("--model", choices=[PACKET_MODEL_ID], help="Specify a model to download.")
    args = parser.parse_args()
    reporter = Reporter()
    driver = dbms_connection(args.drop, reporter)
    if driver is None:
        return

    if args.model == PACKET_MODEL_ID:
        download_model(PACKET_MODEL, reporter)
    else:
        drop_database(driver, args.drop, reporter)
        driver.close()

if __name__ == "__main__":
    main()