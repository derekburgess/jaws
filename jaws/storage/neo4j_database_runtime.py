"""Small Neo4j runtime operations shared by legacy command adapters."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, Protocol, Self, cast


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def single(self) -> _Record | None: ...

    def consume(self) -> Any: ...


class _Session(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Driver(Protocol):
    def session(self, *, database: str) -> _Session: ...


_PROBE_QUERY = "RETURN 1 AS value"

_ENSURE_LOCAL_PERSPECTIVE_QUERY = """
MERGE (address:IP_ADDRESS {IP_ADDRESS: $local_ip})
MERGE (organization:ORGANIZATION {ORGANIZATION: 'YOU ARE HERE'})
MERGE (organization)-[:OWNERSHIP]->(address)
"""


class Neo4jDatabaseRuntime:
    """Connectivity and database bootstrap operations with no domain behavior."""

    def __init__(self, driver: object, database: str) -> None:
        name = database.strip()
        if not name:
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = name

    def probe(self) -> None:
        """Execute and validate the smallest possible database round trip."""

        with self._driver.session(database=self.database) as session:
            row = session.run(_PROBE_QUERY).single()
        if row is None or row["value"] != 1:
            raise RuntimeError("Neo4j connectivity probe returned an unexpected result")

    def ensure_local_perspective(self, local_ip: str) -> None:
        """Idempotently retain the capture host and its compatibility ownership label."""

        address = local_ip.strip()
        if not address:
            raise ValueError("local perspective IP address cannot be empty")
        with self._driver.session(database=self.database) as session:
            session.run(_ENSURE_LOCAL_PERSPECTIVE_QUERY, {"local_ip": address}).consume()
