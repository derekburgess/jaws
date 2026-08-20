"""Compute CLI delegates profile replacement and supports a dependency-free numeric mode."""

from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd

from jaws import jaws_compute
from jaws.ports import InMemoryProfileRepository


class _Driver:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Reporter:
    instances = []

    def __init__(self):
        self.payload = None
        self.summary = None
        self.errors = []
        self.__class__.instances.append(self)

    def info(self, title, message):
        return None

    def error(self, title, message):
        self.errors.append((title, message))

    @contextmanager
    def activity(self, render):
        yield lambda: None

    def result(self, payload, *, summary):
        self.payload = payload
        self.summary = summary


def test_numeric_cli_never_initializes_an_embedding_provider(monkeypatch):
    repository = InMemoryProfileRepository()
    driver = _Driver()
    packets = pd.DataFrame(
        (
            {
                "src_ip": "10.0.0.2",
                "dst_ip": "8.8.8.8",
                "src_port": 50000,
                "dst_port": 443,
                "size": 100,
                "protocol": "TCP",
                "ts_ms": 0,
                "capture_id": "cap_numeric_cli",
            },
        )
    )
    monkeypatch.setattr(jaws_compute, "Reporter", _Reporter)
    monkeypatch.setattr(jaws_compute, "dbms_connection", lambda database, reporter: driver)
    monkeypatch.setattr(
        jaws_compute.Neo4jRepositories,
        "connect",
        lambda driver, database: SimpleNamespace(
            captures=object(),
            packets=object(),
            enrichment=object(),
            profiles=repository,
        ),
    )
    monkeypatch.setattr(
        jaws_compute,
        "resolve_session",
        lambda *args: ("cap_numeric_cli", ["cap_numeric_cli"]),
    )
    monkeypatch.setattr(jaws_compute, "fetch_packets", lambda *args: packets)
    monkeypatch.setattr(jaws_compute, "fetch_ip_metadata", lambda *args: {})
    monkeypatch.setattr(jaws_compute, "prune_profile_sessions", lambda *args: (0, []))
    monkeypatch.setattr(jaws_compute, "count_profile_sessions", lambda *args: 1)
    monkeypatch.setattr(
        jaws_compute,
        "get_openai_client",
        lambda: (_ for _ in ()).throw(AssertionError("OpenAI must not initialize")),
    )
    monkeypatch.setattr(
        jaws_compute,
        "_local_embedding_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("local model must not initialize")),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "jaws-compute",
            "--api",
            "numeric",
            "--database",
            "fixture",
            "--session",
            "latest",
        ],
    )

    jaws_compute.main()

    reporter = _Reporter.instances[-1]
    assert reporter.errors == []
    assert reporter.payload["api"] == "numeric"
    assert reporter.payload["model"] is None
    assert reporter.payload["endpoints_embedded"] == 0
    assert reporter.payload["packets"] == 1
    records = repository.read_scope(jaws_compute.profile_scope_id("cap_numeric_cli"))
    assert len(records) == 2
    assert all(record.identity.model_id is None and record.embedding == () for record in records)
    assert driver.closed is True
