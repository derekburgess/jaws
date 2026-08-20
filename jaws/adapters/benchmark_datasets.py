"""JSON dataset-manifest loading and deterministic synthetic scenario fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from jaws.domain import (
    BenchmarkCandidate,
    BenchmarkPartition,
    BenchmarkPolicy,
    CanonicalDigest,
    CaptureId,
    DatasetId,
    DatasetManifest,
    EntityId,
    EvidencePointer,
    LabelRecord,
    RedistributionPolicy,
    ScenarioData,
    ScenarioManifest,
    SchemaVersion,
)


def load_benchmark_policy(path: Path) -> BenchmarkPolicy:
    document = _object(json.loads(path.read_text(encoding="utf-8")), "benchmark policy")
    return BenchmarkPolicy(
        schema_version=SchemaVersion(str(document.get("schema_version", "1.0.0"))),
        expected_failures=_object(document.get("expected_failures"), "expected failures"),
        accepted_regressions=_object(document.get("accepted_regressions"), "accepted regressions"),
        regression_budgets=_object(document.get("regression_budgets"), "regression budgets"),
        required_baselines=tuple(
            str(item) for item in _array(document.get("required_baselines"), "required baselines")
        ),
        tier_samples=_object(document.get("tier_samples"), "tier samples"),
        forbid_aggregate_recall_only_claims=bool(
            document.get("forbid_aggregate_recall_only_claims", False)
        ),
    )


def load_dataset_manifest(path: Path) -> DatasetManifest:
    document = _object(json.loads(path.read_text(encoding="utf-8")), "dataset manifest")
    scenarios = tuple(_scenario(item) for item in _array(document.get("scenarios"), "scenarios"))
    checksum = document.get("checksum")
    return DatasetManifest(
        schema_version=SchemaVersion(str(document.get("schema_version", "1.0.0"))),
        dataset_id=DatasetId(_string(document, "dataset_id")),
        manifest_version=_string(document, "manifest_version"),
        title=_string(document, "title"),
        source_url=_string(document, "source_url"),
        source_location=_string(document, "source_location"),
        acquisition_date=_date(document.get("acquisition_date")),
        safety_review_date=date.fromisoformat(_string(document, "safety_review_date")),
        license_name=_string(document, "license_name"),
        license_url=_string(document, "license_url"),
        redistribution=RedistributionPolicy(str(document.get("redistribution", "metadata_only"))),
        checksum=_digest(checksum, document),
        checksum_scope=str(document.get("checksum_scope", "source artifact")),
        capture_host=str(document.get("capture_host", "unknown")),
        started_at=_datetime(document.get("started_at")),
        ended_at=_datetime(document.get("ended_at")),
        label_source_version=_string(document, "label_source_version"),
        label_provenance=_string(document, "label_provenance"),
        scenarios=scenarios,
        known_limitations=_strings(document.get("known_limitations", ())),
        contains_malware_binaries=bool(document.get("contains_malware_binaries", False)),
    )


def scenario_data(manifest: DatasetManifest) -> Mapping[str, ScenarioData]:
    """Materialize only controlled synthetic fixtures; real captures stay external."""

    return {
        scenario.scenario_id: ScenarioData(
            scenario,
            _synthetic_candidates(scenario) if scenario.available and scenario.synthetic else (),
        )
        for scenario in manifest.scenarios
    }


def _scenario(value: object) -> ScenarioManifest:
    document = _object(value, "scenario")
    digest = document.get("evidence_digest")
    labels = _labels(document.get("labels"), _string(document, "family"))
    return ScenarioManifest(
        schema_version=SchemaVersion(str(document.get("schema_version", "1.0.0"))),
        scenario_id=_string(document, "scenario_id"),
        family=_string(document, "family"),
        partition=BenchmarkPartition(str(document.get("partition", "development"))),
        capture_ids=tuple(
            CaptureId(str(item)) for item in _array(document.get("capture_ids"), "capture_ids")
        ),
        evidence_digest=_digest(digest, document),
        labels=labels,
        synthetic=bool(document.get("synthetic", False)),
        model_author=(
            str(author) if (author := document.get("model_author")) is not None else None
        ),
        expected_behavior=_string(document, "expected_behavior"),
        known_limitations=_strings(document.get("known_limitations", ())),
        available=bool(document.get("available", True)),
    )


def _synthetic_candidates(scenario: ScenarioManifest) -> tuple[BenchmarkCandidate, ...]:
    seed = int(hashlib.sha256(scenario.scenario_id.encode()).hexdigest()[:8], 16)
    target = next((label.entity_id for label in scenario.labels if label.relevance > 0), None)
    candidates: list[BenchmarkCandidate] = []
    for index, label in enumerate(scenario.labels):
        factor = float(1 + ((seed + index * 17) % 9))
        relevant = label.entity_id == target
        features, history, first_seen = _scenario_features(
            scenario.family, factor, relevant=relevant, benign_decoy=target is None and index == 0
        )
        candidates.append(
            BenchmarkCandidate(
                label.entity_id,
                features,
                history,
                (factor, factor / 3, 12.0 if relevant else 0.0),
                first_seen=first_seen,
                evidence=(
                    EvidencePointer(
                        capture_id=scenario.capture_ids[0],
                        entity_id=label.entity_id,
                        artifact_digest=scenario.evidence_digest,
                        selector=f"generated.entities[{index}]",
                    ),
                ),
            )
        )
    return tuple(candidates)


def _scenario_features(
    family: str, factor: float, *, relevant: bool, benign_decoy: bool
) -> tuple[dict[str, float], dict[str, float], bool]:
    baseline = {
        "bytes_out": factor * 140,
        "bytes_in": factor * 160,
        "packets_out": factor * 14,
        "packets_in": factor * 12,
        "out_peers": factor,
        "in_peers": factor,
        "interval_mean": 10 / factor,
        "interval_cv": 0.2 + 0.01 * factor,
        "rare_port_share": 0.01 * factor,
    }
    features = dict(baseline)
    first_seen = False
    if relevant:
        if family in {"periodic_beacon", "jittered_beacon"}:
            features["interval_cv"] = 0.005 if family == "periodic_beacon" else 0.08
            features["interval_mean"] = 30.0
        elif family in {"burst_exfiltration", "slow_exfiltration"}:
            features["bytes_out"] *= 50 if family == "burst_exfiltration" else 5
            features["bytes_in"] *= 0.1
        elif family == "fan_out_change":
            features["out_peers"] *= 25
        elif family == "first_seen_infrastructure":
            first_seen = True
        elif family == "protocol_port_shift":
            features["rare_port_share"] = 0.95
        elif family == "behavioral_change":
            features["packets_out"] *= 20
            features["out_peers"] *= 10
    elif benign_decoy:
        if family in {"software_updates", "streaming", "cdn_burst"}:
            features["bytes_in"] *= 40
        elif family == "backups":
            features["bytes_out"] *= 40
        elif family == "dns":
            features["packets_out"] *= 30
            features["out_peers"] *= 8
        elif family == "ntp_keepalive":
            features["interval_cv"] = 0.005
            features["interval_mean"] = 30.0
        elif family in {"monitoring", "approved_scan"}:
            features["out_peers"] *= 25
        elif family == "infrastructure_churn":
            first_seen = True
    return features, baseline, first_seen


def _labels(value: object, family: str) -> tuple[LabelRecord, ...]:
    if isinstance(value, Mapping):
        document = _object(value, "label template")
        source_version = _string(document, "source_version")
        benign_count = int(document.get("benign_count", 4))
        if benign_count < 1:
            raise ValueError("label template benign_count must be positive")
        target = bool(document.get("target", False))
        rows = [
            LabelRecord(EntityId(f"benign-{index}"), 0, family, source_version)
            for index in range(1, benign_count + 1)
        ]
        if target:
            rows.insert(0, LabelRecord(EntityId("target"), 3, family, source_version))
        return tuple(rows)
    return tuple(
        LabelRecord(
            EntityId(_string(_object(item, "label"), "entity_id")),
            int(_object(item, "label").get("relevance", 0)),
            _string(_object(item, "label"), "family"),
            _string(_object(item, "label"), "source_version"),
        )
        for item in _array(value, "labels")
    )


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, Any], value)


def _array(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be an array")
    return value


def _string(value: Mapping[str, Any], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return item


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _array(value, "string list"))


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("date must be a string")
    return date.fromisoformat(value)


def _digest(value: object, identity: object) -> CanonicalDigest | None:
    if value is None:
        return None
    if value == "sha256:identity":
        content = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        return CanonicalDigest(hashlib.sha256(content).hexdigest())
    return CanonicalDigest(str(value))
