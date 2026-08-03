"""Opt-in smoke checks for the configured Neo4j research database."""
import pytest

from jaws.config import DATABASE, NEO4J_PASSWORD, get_neo4j_driver


pytestmark = pytest.mark.neo4j


def test_neo4j_connectivity():
    """A configured Neo4j tier must reach the declared database and execute a query."""
    if not NEO4J_PASSWORD:
        pytest.skip("NEO4J_PASSWORD is not set; Neo4j integration tier is unavailable")

    driver = get_neo4j_driver()
    driver.verify_connectivity()
    with driver.session(database=DATABASE) as session:
        assert session.run("RETURN 1 AS value").single()["value"] == 1
