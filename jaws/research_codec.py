"""Stable JSON codec for the research automation interface."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from jaws.domain import (
    CaptureId,
    ComponentSpec,
    DatasetId,
    EntityDefinition,
    EntityId,
    EntityType,
    EvaluationResult,
    EvidencePointer,
    ExperimentSpec,
    FindingId,
    HypothesisSpec,
    ObservationWindow,
    OutlierStatus,
    RankedFinding,
    RankerSpec,
    ReferenceKind,
    ReferenceSpec,
    RepresentationSpec,
    RunId,
    SchemaVersion,
    Score,
    ScoreDirection,
    canonical_json,
    primitive,
    ranked_findings_digest,
)
from jaws.services.research import ComponentDescriptor, ResearchCatalog


def load_document(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("research JSON document must be an object")
    return value


def experiment_document(specification: ExperimentSpec) -> Mapping[str, Any]:
    value = cast(dict[str, Any], primitive(specification))
    return {
        **value,
        "content_digest": str(specification.digest),
        "experiment_id": specification.experiment_id.value,
    }


def decode_experiment_spec(document: Mapping[str, Any]) -> ExperimentSpec:
    hypothesis_value = document.get("hypothesis")
    hypothesis: str | HypothesisSpec
    if isinstance(hypothesis_value, str):
        hypothesis = hypothesis_value
    else:
        hypothesis_data = _object(hypothesis_value, "hypothesis")
        hypothesis = HypothesisSpec(
            schema_version=_schema(hypothesis_data),
            claim=_string(hypothesis_data, "claim"),
            control=_string(hypothesis_data, "control"),
            treatments=_strings(hypothesis_data.get("treatments"), "hypothesis.treatments"),
            metrics=_strings(hypothesis_data.get("metrics"), "hypothesis.metrics"),
            regression_budgets=_mapping(hypothesis_data.get("regression_budgets", {})),
            motivated_by_observation_id=_optional_string(
                hypothesis_data.get("motivated_by_observation_id")
            ),
        )
    observation_data = _object(document.get("observation"), "observation")
    entity_data = _object(document.get("entity", {}), "entity")
    representation_data = _object(document.get("representation"), "representation")
    reference_data = _object(document.get("reference", {}), "reference")
    ranker_data = _object(document.get("ranker"), "ranker")
    specification = ExperimentSpec(
        schema_version=_schema(document),
        hypothesis=hypothesis,
        observation=ObservationWindow(
            schema_version=_schema(observation_data),
            capture_ids=tuple(
                CaptureId(item)
                for item in _strings(observation_data.get("capture_ids"), "capture_ids")
            ),
            started_at=_datetime(observation_data.get("started_at")),
            ended_at=_datetime(observation_data.get("ended_at")),
            perspective=(
                EntityId(value) if (value := observation_data.get("perspective")) else None
            ),
            filters=_strings(observation_data.get("filters", ()), "filters", allow_empty=True),
        ),
        entity=EntityDefinition(
            schema_version=_schema(entity_data),
            entity_type=EntityType(str(entity_data.get("entity_type", "endpoint_ip"))),
            version=str(entity_data.get("version", "1")),
        ),
        representation=RepresentationSpec(
            schema_version=_schema(representation_data),
            representation_id=_string(representation_data, "representation_id"),
            version=str(representation_data.get("version", "1")),
            parameters=_mapping(representation_data.get("parameters", {})),
        ),
        reference=ReferenceSpec(
            schema_version=_schema(reference_data),
            kind=ReferenceKind(str(reference_data.get("kind", "peer"))),
            version=str(reference_data.get("version", "1")),
            parameters=_mapping(reference_data.get("parameters", {})),
        ),
        ranker=RankerSpec(
            schema_version=_schema(ranker_data),
            ranker_id=_string(ranker_data, "ranker_id"),
            version=str(ranker_data.get("version", "1")),
            direction=ScoreDirection(str(ranker_data.get("direction", "higher_is_more_anomalous"))),
            seed=(int(seed) if (seed := ranker_data.get("seed")) is not None else None),
            parameters=_mapping(ranker_data.get("parameters", {})),
        ),
        evaluator=_component(document.get("evaluator"), "standard_metrics"),
        renderer=_component(document.get("renderer"), "json"),
        dataset_ids=tuple(
            DatasetId(item)
            for item in _strings(document.get("dataset_ids", ()), "dataset_ids", allow_empty=True)
        ),
        evidence_digests=_mapping(document.get("evidence_digests", {})),
        label_source_versions=_mapping(document.get("label_source_versions", {})),
        deterministic_settings=_mapping(document.get("deterministic_settings", {})),
    )
    declared_digest = document.get("content_digest") or document.get("experiment_id")
    if declared_digest is not None and str(declared_digest) != str(specification.digest):
        raise ValueError("declared experiment identity does not match canonical content digest")
    return specification


def load_catalog(path: Path) -> ResearchCatalog:
    document = load_document(path)
    catalog = ResearchCatalog(
        datasets=set(_strings(document.get("datasets", ()), "datasets", allow_empty=True)),
        captures=set(_strings(document.get("captures", ()), "captures", allow_empty=True)),
        labels=set(_strings(document.get("labels", ()), "labels", allow_empty=True)),
    )
    components = document.get("components", ())
    if not isinstance(components, Sequence) or isinstance(components, (str, bytes)):
        raise ValueError("catalog components must be an array")
    for item in components:
        data = _object(item, "component")
        catalog.register(
            ComponentDescriptor(
                _string(data, "kind"),
                _string(data, "component_id"),
                _string(data, "version"),
                str(data.get("schema_version", "1.0.0")),
                _strings(
                    data.get("compatible_representations", ()),
                    "compatible_representations",
                    allow_empty=True,
                ),
            )
        )
    return catalog


class DeclarativeResearchEngine:
    """Bounded JSON engine for examples, automation, and deterministic replay."""

    def represent(self, specification: ExperimentSpec, variant: str) -> object:
        fixture = self._fixture(specification, variant)
        return fixture.get("representation", {"entities": fixture.get("ranking", ())})

    def reference(
        self, specification: ExperimentSpec, variant: str, representation: object
    ) -> object:
        return self._fixture(specification, variant).get("reference", {"kind": "declared"})

    def rank(
        self,
        specification: ExperimentSpec,
        variant: str,
        representation: object,
        reference: object,
    ) -> tuple[RankedFinding, ...]:
        assert specification.observation is not None
        ranking = self._fixture(specification, variant).get("ranking")
        if not isinstance(ranking, Sequence) or isinstance(ranking, (str, bytes)) or not ranking:
            raise ValueError(f"fixture ranking must be a nonempty array: {variant}")
        findings: list[RankedFinding] = []
        for rank, item in enumerate(ranking, 1):
            data = _object(item, f"ranking[{rank - 1}]")
            entity_id = _string(data, "entity_id")
            findings.append(
                RankedFinding(
                    finding_id=FindingId(str(data.get("finding_id", f"{variant}-{entity_id}"))),
                    entity_id=EntityId(entity_id),
                    rank=rank,
                    score=Score(
                        float(data.get("score", len(ranking) - rank + 1)),
                        specification.ranker.direction,  # type: ignore[union-attr]
                    ),
                    outlier=OutlierStatus(str(data.get("outlier", "not_scored"))),
                    evidence=(
                        EvidencePointer(
                            capture_id=specification.observation.capture_ids[0],
                            entity_id=EntityId(entity_id),
                            selector="ranked_finding",
                        ),
                    ),
                )
            )
        return tuple(findings)

    def evaluate(
        self,
        specification: ExperimentSpec,
        variant: str,
        run_id: RunId,
        findings: tuple[RankedFinding, ...],
    ) -> EvaluationResult:
        metrics = _mapping(self._fixture(specification, variant).get("metrics", {}))
        return EvaluationResult(
            run_id=run_id,
            evaluator_id=specification.evaluator.component_id,
            evaluator_version=specification.evaluator.version,
            metrics={key: float(value) for key, value in metrics.items()},
            ranked_entity_ids=tuple(
                item.entity_id for item in findings if item.entity_id is not None
            ),
            findings_digest=ranked_findings_digest(findings),
        )

    @staticmethod
    def _fixture(specification: ExperimentSpec, variant: str) -> Mapping[str, Any]:
        fixtures = _mapping(specification.deterministic_settings.get("fixtures", {}))
        fixture = fixtures.get(variant)
        return _object(fixture, f"deterministic_settings.fixtures.{variant}")


def dumps(value: object) -> str:
    return canonical_json(value) + "\n"


def _component(value: object, default_id: str) -> ComponentSpec:
    if value is None:
        return ComponentSpec(component_id=default_id)
    data = _object(value, default_id)
    return ComponentSpec(
        schema_version=_schema(data),
        component_id=str(data.get("component_id", default_id)),
        version=str(data.get("version", "1")),
        parameters=_mapping(data.get("parameters", {})),
    )


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, Any], value)


def _mapping(value: object) -> Mapping[str, Any]:
    return _object(value, "value")


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return item


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("optional string cannot be empty")
    return value


def _strings(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be an array")
    result = tuple(str(item).strip() for item in value)
    if (not allow_empty and not result) or any(not item for item in result):
        raise ValueError(f"{name} must contain nonempty strings")
    return result


def _schema(value: Mapping[str, Any]) -> SchemaVersion:
    return SchemaVersion(str(value.get("schema_version", "1.0.0")))


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
