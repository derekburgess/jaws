"""Contract tests for the versioned Benchmark 0 measurement bundle."""

import copy
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from harness.benchmark_contract import (
    ContractViolation,
    DOCUMENT_FILES,
    SCHEMA_FILES,
    SCHEMA_SOURCE,
    _read_jsonl,
    render_report,
    validate_bundle,
    write_checksums,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "benchmarks" / "examples" / "baseline-0"
SYNTHETIC_SCENARIOS = {
    "payload_beacon",
    "jittered_beacon",
    "tcp_keepalive",
    "slow_exfil",
    "burst_exfil",
    "bulk_download",
    "stable_heavy",
    "behavioral_change",
}
PCAP_SCENARIOS = {"njrat_c2", "masslogger_exfil", "njrat_ipcheck"}


def _copy_bundle(tmp_path):
    target = tmp_path / "bundle"
    shutil.copytree(EXAMPLE, target)
    return target


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_rankings(path, rows):
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def test_all_schema_documents_are_valid_draft_2020_12():
    assert set(SCHEMA_FILES) == set(DOCUMENT_FILES) | {"ranking"}
    for filename in SCHEMA_FILES.values():
        schema = _read(SCHEMA_SOURCE / filename)
        Draft202012Validator.check_schema(schema)


def test_committed_contract_example_validates():
    validate_bundle(EXAMPLE)


def test_example_retains_every_synthetic_ranking_and_explicit_pcap_skips():
    scenarios = _read(EXAMPLE / "scenarios.json")["scenarios"]
    rankings = _read_jsonl(EXAMPLE / "rankings.jsonl")
    scenario_map = {row["scenario_id"]: row for row in scenarios}
    ranking_map = {row["scenario_id"]: row for row in rankings}

    assert SYNTHETIC_SCENARIOS <= set(scenario_map)
    assert PCAP_SCENARIOS <= set(scenario_map)
    assert {
        ranking_map[name]["evaluation_surface"] for name in SYNTHETIC_SCENARIOS
    } == {"endpoints", "host_outbound"}

    for name in SYNTHETIC_SCENARIOS:
        ranking = ranking_map[name]
        assert ranking["execution_status"] == "completed"
        assert ranking["ranking_complete"] is True
        assert ranking["findings"]
        assert [row["rank"] for row in ranking["findings"]] == list(
            range(1, len(ranking["findings"]) + 1))

    for name in PCAP_SCENARIOS:
        ranking = ranking_map[name]
        assert ranking["execution_status"] == "skipped"
        assert ranking["quality_outcome"] == "not_evaluated"
        assert ranking["skip_reason"].startswith("dataset_unavailable:")
        assert ranking["findings"] == []


def test_example_distinguishes_passes_from_known_quality_failures():
    evaluation = _read(EXAMPLE / "evaluation.json")
    outcomes = {
        row["scenario_id"]: row["quality_outcome"]
        for row in evaluation["per_scenario"]
    }
    assert {
        name for name, outcome in outcomes.items() if outcome == "passed"
    } == {
        "payload_beacon",
        "jittered_beacon",
        "slow_exfil",
        "burst_exfil",
        "behavioral_change",
    }
    assert {
        name for name, outcome in outcomes.items() if outcome == "failed_known"
    } == {"tcp_keepalive", "bulk_download", "stable_heavy"}


def test_example_retains_full_numeric_profiles_and_prior_session_evidence():
    scenarios = {
        row["scenario_id"]: row
        for row in _read(EXAMPLE / "scenarios.json")["scenarios"]
    }
    rankings = {
        row["scenario_id"]: row for row in _read_jsonl(EXAMPLE / "rankings.jsonl")
    }
    expected_features = {
        "bytes_out",
        "bytes_in",
        "packets_out",
        "packets_in",
        "out_peers",
        "in_peers",
        "bytes_out_in_ratio",
        "packets_out_in_ratio",
        "bytes_per_packet",
        "bytes_per_peer",
        "interval_mean",
        "interval_cv",
    }
    endpoint_finding = rankings["payload_beacon"]["findings"][0]
    assert set(endpoint_finding["attributes"]["numeric_features"]) == expected_features
    assert "out_ports" in endpoint_finding["attributes"]
    assert "in_ports" in endpoint_finding["attributes"]
    assert "protocols" in endpoint_finding["attributes"]

    for name in ("stable_heavy", "behavioral_change"):
        history = scenarios[name]["history"]
        assert history["prior_sessions"] == 3
        assert len(history["sessions"]) == 3
        assert all(len(row["packet_rows_sha256"]) == 64 for row in history["sessions"])
        assert all(row["packet_count"] > 0 for row in history["sessions"])


def test_report_is_derived_only_from_machine_readable_records():
    assert (EXAMPLE / "report.md").read_text(encoding="utf-8") == render_report(EXAMPLE)


@pytest.mark.parametrize(
    ("mode", "state"),
    [
        ("text-only", "used"),
        ("text-only", "unavailable"),
        ("numeric-only", "not_used"),
        ("numeric-only", "unsupported"),
        ("blended", "used"),
        ("blended", "unavailable"),
    ],
)
def test_run_schema_represents_feature_modes_and_availability(mode, state):
    schema = _read(SCHEMA_SOURCE / SCHEMA_FILES["run"])
    run = _read(EXAMPLE / "run.json")
    candidate = copy.deepcopy(run)
    candidate["analytical_modes"][0] = {
        "name": mode,
        "state": state,
        "reason": "contract test",
    }
    Draft202012Validator(schema).validate(candidate)


def test_unknown_schema_version_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = _read(manifest_path)
    manifest["schema_version"] = "99.0.0"
    _write(manifest_path, manifest)
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="1.0.0"):
        validate_bundle(bundle)


def test_missing_required_field_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    environment_path = bundle / "environment.json"
    environment = _read(environment_path)
    del environment["python"]["version"]
    _write(environment_path, environment)
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="version"):
        validate_bundle(bundle)


def test_noncontiguous_rank_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    rankings_path = bundle / "rankings.jsonl"
    rankings = _read_jsonl(rankings_path)
    rankings[0]["findings"][0]["rank"] = 2
    _write_rankings(rankings_path, rankings)
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="contiguous and one-based"):
        validate_bundle(bundle)


def test_reason_value_drift_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    rankings_path = bundle / "rankings.jsonl"
    rankings = _read_jsonl(rankings_path)
    reason = rankings[0]["findings"][0]["reasons"][0]
    reason["value"] += 1.0
    _write_rankings(rankings_path, rankings)
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="reason value does not match"):
        validate_bundle(bundle)


def test_corrupted_checksum_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    with (bundle / "logs" / "collector.stdout.log").open("a", encoding="utf-8") as stream:
        stream.write("corruption\n")

    with pytest.raises(ContractViolation, match="checksum mismatch"):
        validate_bundle(bundle)


def test_generated_report_drift_is_rejected(tmp_path):
    bundle = _copy_bundle(tmp_path)
    with (bundle / "report.md").open("a", encoding="utf-8") as stream:
        stream.write("\nmanual interpretation\n")
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="deterministic rendering"):
        validate_bundle(bundle)


def test_secret_sentinel_is_rejected_even_with_valid_checksums(tmp_path):
    bundle = _copy_bundle(tmp_path)
    with (bundle / "logs" / "collector.stderr.log").open("a", encoding="utf-8") as stream:
        stream.write("sk-jawscontractsentinel12345\n")
    write_checksums(bundle)

    with pytest.raises(ContractViolation, match="potential secret material"):
        validate_bundle(bundle)


def test_environment_contract_cannot_store_variable_values():
    schema = _read(SCHEMA_SOURCE / SCHEMA_FILES["environment"])
    environment = _read(EXAMPLE / "environment.json")
    candidate = copy.deepcopy(environment)
    candidate["environment_variables"][0]["value"] = "not-allowed"

    errors = list(Draft202012Validator(schema).iter_errors(candidate))
    assert any("Additional properties are not allowed" in error.message for error in errors)
