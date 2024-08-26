import os
import argparse
import pandas as pd
from neo4j import GraphDatabase

uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")


def connect_to_database(uri, username, password, database):
    return GraphDatabase.driver(uri, auth=(username, password))


def preview_database(driver, database):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        return count


def clear_database(driver, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))


def main():
    parser = argparse.ArgumentParser(description="Clear the Neo4j database")
    parser.add_argument("--database", default="captures", help="Specify the Neo4j database to clear (default: captures)")

    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)

    record_count = preview_database(driver, args.database)
    confirm = input(f"\nAre you sure you want to clear the {record_count} records from the database? Enter 'YES' to confirm: ")
    if confirm == 'YES':
        print("\nClearing the Neo4j database...")
        clear_database(driver, args.database)

    driver.close()

if __name__ == "__main__":
    main()