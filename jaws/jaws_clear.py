import os
import argparse
from neo4j import GraphDatabase

uri = os.getenv("NEO4J_URI")
username = os.getenv("NEO4J_USERNAME")
password = os.getenv("NEO4J_PASSWORD")


def connect_to_database(uri, username, password, database):
    return GraphDatabase.driver(uri, auth=(username, password))


def clear_database(driver, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))


def main():
    parser = argparse.ArgumentParser(description="Clear the Neo4j database")
    parser.add_argument("--database", default="captures",
                        help="Specify the Neo4j database to clear (default: captures)")

    args = parser.parse_args()
    driver = connect_to_database(uri, username, password, args.database)

    print("\nClearing the Neo4j database...")
    clear_database(driver, args.database)

    driver.close()


if __name__ == "__main__":
    main()