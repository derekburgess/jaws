"""Offline contracts for the remaining database-runtime storage operations."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from jaws.storage import Neo4jDatabaseRuntime


@dataclass
class FakeResult:
    records: list[dict[str, object]] = field(default_factory=list)
    consumed: bool = False

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self.records)

    def single(self) -> dict[str, object] | None:
        return self.records[0] if self.records else None

    def consume(self) -> None:
        self.consumed = True


@dataclass
class RecordingDriver:
    probe_value: object = 1
    databases: list[str] = field(default_factory=list)
    calls: list[tuple[str, Mapping[str, object] | None, FakeResult]] = field(default_factory=list)

    def session(self, *, database: str) -> RecordingDriver:
        self.databases.append(database)
        return self

    def __enter__(self) -> RecordingDriver:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def run(
        self,
        query: str,
        parameters: Mapping[str, object] | None = None,
    ) -> FakeResult:
        normalized = " ".join(query.split())
        result = (
            FakeResult([{"value": self.probe_value}])
            if normalized == "RETURN 1 AS value"
            else FakeResult()
        )
        self.calls.append((normalized, parameters, result))
        return result


def test_probe_targets_the_exact_database_and_validates_the_round_trip():
    driver = RecordingDriver()

    Neo4jDatabaseRuntime(driver, "captures").probe()

    assert driver.databases == ["captures"]
    assert driver.calls[0][:2] == ("RETURN 1 AS value", None)


@pytest.mark.parametrize("value", (None, 0, "1"))
def test_probe_rejects_an_unexpected_database_result(value: Any):
    driver = RecordingDriver(probe_value=value)

    with pytest.raises(RuntimeError, match="unexpected result"):
        Neo4jDatabaseRuntime(driver, "captures").probe()


def test_local_perspective_seed_is_parameterized_and_consumed():
    driver = RecordingDriver()
    runtime = Neo4jDatabaseRuntime(driver, "captures")

    runtime.ensure_local_perspective(" 192.0.2.10 ")

    query, parameters, result = driver.calls[0]
    assert "MERGE (address:IP_ADDRESS" in query
    assert "MERGE (organization:ORGANIZATION" in query
    assert "MERGE (organization)-[:OWNERSHIP]->(address)" in query
    assert parameters == {"local_ip": "192.0.2.10"}
    assert result.consumed


@pytest.mark.parametrize("database", ("", "   "))
def test_database_runtime_requires_an_exact_nonempty_target(database: str):
    with pytest.raises(ValueError, match="database name"):
        Neo4jDatabaseRuntime(RecordingDriver(), database)


def test_local_perspective_requires_a_nonempty_address():
    with pytest.raises(ValueError, match="local perspective"):
        Neo4jDatabaseRuntime(RecordingDriver(), "captures").ensure_local_perspective(" ")
