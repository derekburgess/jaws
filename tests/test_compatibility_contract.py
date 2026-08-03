"""Tests for the Benchmark 0 CLI and Neo4j compatibility collectors."""

import copy
import json

import pytest

from harness.benchmark_contract import ContractViolation, _resolve_commit
from harness.compatibility_contract import (
    EXPECTED_CLI_CASES,
    collect_cli_contract,
    collect_graph_schema_inventory,
    validate_cli_contract,
    validate_graph_schema_inventory,
)


SUBJECT = _resolve_commit("0b68a8c")
COLLECTOR = "worktree"


@pytest.fixture(scope="module")
def cli_contract():
    return collect_cli_contract(SUBJECT, COLLECTOR)


@pytest.fixture(scope="module")
def graph_inventory():
    return collect_graph_schema_inventory(SUBJECT, COLLECTOR)


def test_cli_contract_covers_success_rank_surfaces_and_failures(cli_contract):
    cases = {row["case_id"]: row for row in cli_contract["cases"]}
    assert set(cases) == EXPECTED_CLI_CASES
    assert cases["capture-list-success"]["parsed_stdout"] == {
        "ok": True,
        "interfaces": ["en0", "eth0"],
    }
    assert cases["compute-success"]["parsed_stdout"]["endpoints_embedded"] == 2
    ranking = cases["rank-success"]["parsed_stdout"]
    assert ranking["endpoints"]
    assert ranking["host_outbound"]["destinations"]


def test_cli_contract_records_current_error_exit_split(cli_contract):
    cases = {row["case_id"]: row for row in cli_contract["cases"]}
    for name in (
        "compute-unknown-session",
        "capture-missing-file",
        "compute-neo4j-unavailable",
    ):
        assert cases[name]["exit_code"] == 0
        assert cases[name]["parsed_stdout"]["ok"] is False
    argument = cases["capture-invalid-duration"]
    assert argument["exit_code"] == 2
    assert argument["stdout"] == ""
    assert argument["parsed_stdout"] is None


def test_cli_contract_rejects_tampered_stream_digest(cli_contract):
    candidate = copy.deepcopy(cli_contract)
    candidate["cases"][0]["stdout"] += "corruption"
    with pytest.raises(ContractViolation, match="stdout digest mismatch"):
        validate_cli_contract(candidate)


def test_graph_inventory_covers_all_observed_schema_elements(graph_inventory):
    assert graph_inventory["statistics"] == {
        "labels": 6,
        "node_properties": 40,
        "relationship_types": 5,
        "relationship_properties": 0,
        "constraints": 3,
        "indexes": 5,
        "cypher_source_locations": 44,
    }
    assert graph_inventory["observed_sets"]["labels"] == [
        "CAPTURE",
        "ENDPOINT",
        "IP_ADDRESS",
        "ORGANIZATION",
        "PACKET",
        "PORT",
    ]
    assert graph_inventory["observed_sets"]["relationships"] == [
        "OWNERSHIP",
        "PORT",
        "PROFILE",
        "RECEIVED",
        "SENT",
    ]


def test_graph_inventory_retains_current_constraints_and_indexes(graph_inventory):
    assert {row["name"] for row in graph_inventory["constraints"]} == {
        "capture_id_unique",
        "ip_address_unique",
        "organization_unique",
    }
    assert {row["name"] for row in graph_inventory["indexes"]} == {
        "endpoint_capture_index",
        "endpoint_ip_index",
        "packet_capture_index",
        "packet_timestamp_index",
        "port_composite_index",
    }


def test_graph_inventory_rejects_property_drift(graph_inventory):
    candidate = copy.deepcopy(graph_inventory)
    candidate["nodes"][0]["properties"].append(
        {"name": "INVENTED", "observed_type": "string"}
    )
    with pytest.raises(ContractViolation, match="declared node inventory drifted"):
        validate_graph_schema_inventory(candidate)


def test_contract_documents_are_canonical_json_serializable(
    cli_contract, graph_inventory
):
    for document in (cli_contract, graph_inventory):
        encoded = json.dumps(document, sort_keys=True, allow_nan=False)
        assert json.loads(encoded) == document
