import argparse
from rich.text import Text
from rich.panel import Panel
from transformers import AutoTokenizer, AutoModelForCausalLM
from jaws.jaws_config import *


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


# Drops all entities from the database.
def drop_database(driver, database):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        if count == 0:
            return CONSOLE.print(render_info_panel("INFO", f"'{database}' is empty.", CONSOLE))
        CONSOLE.print(render_info_panel("CONFIRM", f"Are you sure you want to drop({count}): '{database}'?", CONSOLE))
        confirm = input("Enter 'YES' to confirm: ")
        if confirm == 'YES':
            session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
        return CONSOLE.print(render_success_panel("PROCESS COMPLETE", f"Dropped({count}): '{database}'", CONSOLE))


def main():
    parser = argparse.ArgumentParser(description="Utility functions for JAWS | 1.) Download models 2.) Drop database")
    parser.add_argument("--drop", default=DATABASE, help=f"Specify a database to drop (default: '{DATABASE}').")
    parser.add_argument("--model", choices=[PACKET_MODEL_ID, LANG_MODEL_ID], help="Specify a model to download.")
    args = parser.parse_args()
    driver = dbms_connection(args.drop)
    if driver is None:
        return

    if args.model == PACKET_MODEL_ID:
        download_model(PACKET_MODEL)
    elif args.model == LANG_MODEL_ID:
        download_model(LANG_MODEL)
    else: 
        drop_database(driver, args.drop)
        driver.close()

if __name__ == "__main__":
    main()