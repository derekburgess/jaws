import argparse
from neo4j.exceptions import ServiceUnavailable
from jaws.jaws_config import *


def dbms_connection(database):
    try:
        with NEO4J.session(database=database) as session:
            session.run("RETURN 1")
        return NEO4J
    except ServiceUnavailable:
        raise Exception(f"Unable to connect to Neo4j database. Please check your connection settings.")
    except Exception as e:
        if "database does not exist" in str(e).lower():
            raise Exception(f"{database} database not found. You need to create the default '{DATABASE}' database or pass an existing database name.")
        else:
            raise


def preview_database(driver, database):
    with driver.session(database=database) as session:
        result = session.run("MATCH (n) RETURN count(n)")
        count = result.single()[0]
        return count


def clear_database(driver, database):
    with driver.session(database=database) as session:
        session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))


def main():
    parser = argparse.ArgumentParser(description="Clear the Neo4j database.")
    parser.add_argument("--database", default=DATABASE, help=f"Specify the Neo4j database to clear (default: {DATABASE}).")

    args = parser.parse_args()

    try:
        driver = dbms_connection(args.database)
        record_count = preview_database(driver, args.database)
    except Exception as e:
        print(f"\n{args.database} not found.")
        print(f"You either need to create the default '{DATABASE}' database or pass an existing database name.", "\n")
        return

    confirm = input(f"\nAre you sure you want to clear the {record_count} records from the database? Enter 'YES' to confirm: ")
    if confirm == 'YES':
        print("\nClearing the Neo4j database...", "\n")
        clear_database(driver, args.database)

    driver.close()

if __name__ == "__main__":
    main()