import argparse
from rich.text import Text
from rich.panel import Panel
from rich.console import Console
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.jaws_config import *


# Utility functions imported elsewhere.
def render_error_panel(title, message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"[{title}]", border_style="red", width=width)


def render_info_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"[{title}]", border_style="yellow", width=width)


def render_success_panel(title,message, console):
    width = console.size.width
    return Panel(Text(message, justify="center"), title=f"[{title}]", border_style="green", width=width)


def render_activity_panel(title, recent_packets, console, height=10):
    width = console.size.width
    lines = recent_packets[-(height-2):]
    while len(lines) < (height-2):
        lines.insert(0, "")
    text = "\n".join(lines)
    return Panel(Text(text, justify="center"), title=f"[{title}]", border_style="blue", width=width, height=height)


# Used for the message panels below.
console = Console()

# Downloads the models to the local device. Nice if you do not want to wait for model downloads on first compute.
def download_model(model):
    try:
        message = f"Downloading model: {model}"
        console.print(render_info_panel("INFORMATION", message, console))
        AutoTokenizer.from_pretrained(model)
        AutoModelForCausalLM.from_pretrained(model)
        message = f"Successfully downloaded model: {model}"
        console.print(render_success_panel("SUCCESS", message, console))
    except Exception as e:
        message = f"{model}\n\n{str(e)}"
        console.print(render_error_panel("ERROR", message, console))


# Support function, imported heavily throughtout the project.
def dbms_connection(database):
    try:
        with NEO4J.session(database=database) as session:
            session.run("RETURN 1")
        return NEO4J
    except Exception as e:
        if "database does not exist" in str(e).lower():
            message = f"{database} database does not exist. You need to create the default '{DATABASE}' database or provide the name of an existing database."
            console.print(render_error_panel("ERROR", message, console))
        return None


# Drops the database of all entities.
def drop_database(driver, database):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        message = f"Are you sure you want to clear the {count} records from '{database}'? Enter 'YES' to confirm:"
        console.print(render_info_panel("CONFIRMATION", message, console))
        confirm = input("> ")
        if confirm == 'YES':
            session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
            message = f"Successfully cleared {count} records from: '{database}'"
            console.print(render_success_panel("SUCCESS", message, console))
        return count


def main():
    parser = argparse.ArgumentParser(description="Utility functions for JAWS | 1.) Download models 2.) Drop database")
    parser.add_argument("--drop", default=DATABASE, help=f"Specify a database to drop (default: '{DATABASE}').")
    parser.add_argument("--model", choices=[PACKET_MODEL_ID, LANG_MODEL_ID], help="Specify a model to download.")
    args = parser.parse_args()

    if args.model == PACKET_MODEL_ID:
        download_model(PACKET_MODEL)
    elif args.model == LANG_MODEL_ID:
        download_model(LANG_MODEL)
    else:
        driver = dbms_connection(args.drop)
        if driver is None:
            return
        drop_database(driver, args.drop)
        driver.close()

if __name__ == "__main__":
    main()