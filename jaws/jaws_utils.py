import argparse
from rich.text import Text
from rich.panel import Panel
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.config import *


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


# Downloads the models to the local device.
# Nice if you do not want to wait for model downloads on first compute.
def download_model(model):
    try:
        CONSOLE.print(render_info_panel("INFO", f"Downloading: {model}", CONSOLE))
        AutoTokenizer.from_pretrained(model)
        AutoModelForCausalLM.from_pretrained(model)
        CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Downloaded: {model}", CONSOLE))
    except Exception as e:
        CONSOLE.print(render_error_panel("ERROR", f"{model}\n\n{str(e)}", CONSOLE))


# Support function, imported heavily throughtout the project.
def dbms_connection(database):
    try:
        with NEO4J.session(database=database) as session:
            session.run("RETURN 1")
        return NEO4J
    except Exception as e:
        if "database does not exist" in str(e).lower():
            error = f"'{database}' database does not exist.\nYou need to create the default '{DATABASE}' database or provide the name of an existing database."
            CONSOLE.print(render_error_panel("ERROR", error, CONSOLE))
        return None

# Populate database with schema. Called prior to capture.
def initialize_schema(driver, database):
    schema_definitions = [
        {
            "type": "constraint",
            "name": "ip_address_unique",
            "label": "IP_ADDRESS",
            "properties": ["IP_ADDRESS"],
            "query": "CREATE CONSTRAINT ip_address_unique FOR (ip:IP_ADDRESS) REQUIRE ip.IP_ADDRESS IS UNIQUE"
        },
        {
            "type": "constraint",
            "name": "organization_unique",
            "label": "ORGANIZATION",
            "properties": ["ORGANIZATION"],
            "query": "CREATE CONSTRAINT organization_unique FOR (org:ORGANIZATION) REQUIRE org.ORGANIZATION IS UNIQUE"
        },
        {
            "type": "index",
            "name": "port_composite_index",
            "label": "PORT",
            "properties": ["PORT", "IP_ADDRESS"],
            "query": "CREATE INDEX port_composite_index FOR (p:PORT) ON (p.PORT, p.IP_ADDRESS)"
        },
        {
            "type": "index",
            "name": "traffic_composite_index",
            "label": "TRAFFIC",
            "properties": ["IP_ADDRESS", "PORT"],
            "query": "CREATE INDEX traffic_composite_index FOR (t:TRAFFIC) ON (t.IP_ADDRESS, t.PORT)"
        }
    ]
    
    with driver.session(database=database) as session:
        for schema in schema_definitions:
            try:
                session.run(schema["query"])
                msg = f"Created {schema['type']} '{schema['name']}' on {schema['label']}({', '.join(schema['properties'])})"
                CONSOLE.print(render_success_panel("SCHEMA", msg, CONSOLE))
            except Exception as e:
                CONSOLE.print(render_error_panel("ERROR", {e}, CONSOLE))


# Drops all entities from the database.
def drop_database(driver, database, agent):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        if count == 0:
            if not agent:
                return CONSOLE.print(render_info_panel("INFO", f"'{database}' is empty.", CONSOLE))
            else:
                return print(f"[INFO] '{database}' is empty.")
        else:
            session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
            if not agent:
                return CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Dropped({count}): '{database}'", CONSOLE))
            else:
                return print(f"[PROCESS COMPLETE] Dropped({count}): '{database}'")


def main():
    parser = argparse.ArgumentParser(description="Utility functions for JAWS | 1.) Download models 2.) Drop database")
    parser.add_argument("--drop", default=DATABASE, help=f"Specify a database to drop (default: '{DATABASE}').")
    parser.add_argument("--model", choices=[PACKET_MODEL_ID], help="Specify a model to download.")
    parser.add_argument("--agent", action="store_true", help="Disable rich output for agent use.")
    args = parser.parse_args()
    driver = dbms_connection(args.drop)
    if driver is None:
        return

    if args.model == PACKET_MODEL_ID:
        download_model(PACKET_MODEL)
    else: 
        drop_database(driver, args.drop, args.agent)
        driver.close()

if __name__ == "__main__":
    main()