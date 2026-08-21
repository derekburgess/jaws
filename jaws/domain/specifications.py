"""Immutable, versioned research specifications and evidence joins."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from string import Formatter
from types import MappingProxyType
from typing import Any, Mapping

from .enums import (
    EntityType,
    MeasurementUnit,
    MetricObjective,
    MissingValuePolicy,
    NumericAnalysisTransformation,
    NumericFeatureFamily,
    NumericFeatureTransformation,
    OutlierStatus,
    ReferenceKind,
    ScoreDirection,
    TextSequenceFormat,
    TimingDirection,
    TimingDirectionSelection,
    TimingIntervalScope,
)
from .identifiers import (
    CanonicalDigest,
    CaptureId,
    DatasetId,
    EntityId,
    ExperimentId,
    FindingId,
    SchemaVersion,
)
from .profiles import MIN_TIMING_PACKETS
from .serialization import canonical_digest
from .time import normalize_utc

Parameters = Mapping[str, Any]


def _immutable_mapping(value: Parameters) -> Parameters:
    return MappingProxyType({key: _immutable_value(item) for key, item in value.items()})


def _immutable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_immutable_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class VersionedSpec:
    schema_version: SchemaVersion = SchemaVersion("1.0.0")

    @property
    def digest(self) -> CanonicalDigest:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class ObservationWindow(VersionedSpec):
    capture_ids: tuple[CaptureId, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None
    perspective: EntityId | None = None
    filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capture_ids:
            raise ValueError("observation window requires at least one capture")
        if len(set(self.capture_ids)) != len(self.capture_ids):
            raise ValueError("observation window capture IDs must be unique")
        if self.started_at is not None:
            object.__setattr__(self, "started_at", normalize_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", normalize_utc(self.ended_at))
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("observation window ends before it starts")
        filters = tuple(value.strip() for value in self.filters)
        if any(not value for value in filters):
            raise ValueError("observation window filters cannot be empty")
        object.__setattr__(self, "filters", filters)


@dataclass(frozen=True, slots=True)
class EntityDefinition(VersionedSpec):
    entity_type: EntityType = EntityType.ENDPOINT_IP
    version: str = "1"

    def __post_init__(self) -> None:
        version = self.version.strip()
        if not version:
            raise ValueError("entity definition version cannot be empty")
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, slots=True)
class NumericFeatureDefinition:
    """One ordered numeric value and the exact rule that produces it."""

    name: str
    family: NumericFeatureFamily
    unit: MeasurementUnit
    transformation: NumericFeatureTransformation
    numerator_fields: tuple[str, ...]
    denominator_fields: tuple[str, ...] = ()
    denominator_offset: float = 0.0
    missing_value_policy: MissingValuePolicy = MissingValuePolicy.FORBID
    analysis_transformation: NumericAnalysisTransformation = NumericAnalysisTransformation.LOG1P

    def __post_init__(self) -> None:
        name = self.name.strip()
        numerator = tuple(value.strip() for value in self.numerator_fields)
        denominator = tuple(value.strip() for value in self.denominator_fields)
        if not name:
            raise ValueError("numeric feature name cannot be empty")
        if not numerator or any(not value for value in numerator):
            raise ValueError("numeric feature numerator fields cannot be empty")
        if len(set(numerator + denominator)) != len(numerator + denominator):
            raise ValueError("numeric feature source fields must be unique")
        if any(not value for value in denominator):
            raise ValueError("numeric feature denominator fields cannot be empty")
        if self.transformation is NumericFeatureTransformation.IDENTITY:
            if len(numerator) != 1 or denominator or self.denominator_offset != 0.0:
                raise ValueError("identity numeric features require exactly one source field")
        elif self.transformation is NumericFeatureTransformation.SAFE_RATIO:
            if (
                not denominator
                or not isfinite(self.denominator_offset)
                or self.denominator_offset <= 0.0
            ):
                raise ValueError(
                    "safe-ratio features require denominator fields and positive offset"
                )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "numerator_fields", numerator)
        object.__setattr__(self, "denominator_fields", denominator)


@dataclass(frozen=True, slots=True)
class TimingEvidenceRequirement:
    """Evidence and direction policy shared by timing features in a representation."""

    directions: tuple[TimingDirection, ...]
    selection: TimingDirectionSelection
    minimum_packets_per_direction: int
    interval_scope: TimingIntervalScope

    def __post_init__(self) -> None:
        directions = tuple(self.directions)
        if not directions or len(set(directions)) != len(directions):
            raise ValueError("timing evidence directions must be nonempty and unique")
        if self.minimum_packets_per_direction < 2:
            raise ValueError("timing evidence requires at least two packets per direction")
        object.__setattr__(self, "directions", directions)


@dataclass(frozen=True, slots=True)
class NumericFeatureSet(VersionedSpec):
    """Versioned, ordered numeric representation contract."""

    feature_set_id: str = ""
    version: str = "1"
    features: tuple[NumericFeatureDefinition, ...] = ()
    timing_evidence: TimingEvidenceRequirement | None = None

    def __post_init__(self) -> None:
        feature_set_id = self.feature_set_id.strip()
        version = self.version.strip()
        features = tuple(self.features)
        if not feature_set_id:
            raise ValueError("numeric feature-set ID cannot be empty")
        if not version:
            raise ValueError("numeric feature-set version cannot be empty")
        if not features:
            raise ValueError("numeric feature set requires at least one feature")
        names = tuple(feature.name for feature in features)
        if len(set(names)) != len(names):
            raise ValueError("numeric feature names must be unique")
        has_timing_features = any(
            feature.family is NumericFeatureFamily.TIMING for feature in features
        )
        if has_timing_features != (self.timing_evidence is not None):
            raise ValueError(
                "numeric feature sets with timing features require timing evidence metadata"
            )
        object.__setattr__(self, "feature_set_id", feature_set_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "features", features)

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(feature.name for feature in self.features)


ENDPOINT_NUMERIC_FEATURE_SET_V1 = NumericFeatureSet(
    feature_set_id="endpoint_profile_numeric",
    version="1",
    features=(
        NumericFeatureDefinition(
            "bytes_out",
            NumericFeatureFamily.BASE,
            MeasurementUnit.BYTES,
            NumericFeatureTransformation.IDENTITY,
            ("bytes_out",),
        ),
        NumericFeatureDefinition(
            "bytes_in",
            NumericFeatureFamily.BASE,
            MeasurementUnit.BYTES,
            NumericFeatureTransformation.IDENTITY,
            ("bytes_in",),
        ),
        NumericFeatureDefinition(
            "packets_out",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PACKETS,
            NumericFeatureTransformation.IDENTITY,
            ("packets_out",),
        ),
        NumericFeatureDefinition(
            "packets_in",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PACKETS,
            NumericFeatureTransformation.IDENTITY,
            ("packets_in",),
        ),
        NumericFeatureDefinition(
            "out_peers",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PEERS,
            NumericFeatureTransformation.IDENTITY,
            ("out_peers",),
        ),
        NumericFeatureDefinition(
            "in_peers",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PEERS,
            NumericFeatureTransformation.IDENTITY,
            ("in_peers",),
        ),
        NumericFeatureDefinition(
            "bytes_out_in_ratio",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.RATIO,
            NumericFeatureTransformation.SAFE_RATIO,
            ("bytes_out",),
            ("bytes_in",),
            1.0,
        ),
        NumericFeatureDefinition(
            "packets_out_in_ratio",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.RATIO,
            NumericFeatureTransformation.SAFE_RATIO,
            ("packets_out",),
            ("packets_in",),
            1.0,
        ),
        NumericFeatureDefinition(
            "bytes_per_packet",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.BYTES_PER_PACKET,
            NumericFeatureTransformation.SAFE_RATIO,
            ("bytes_out", "bytes_in"),
            ("packets_out", "packets_in"),
            1.0,
        ),
        NumericFeatureDefinition(
            "bytes_per_peer",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.BYTES_PER_PEER,
            NumericFeatureTransformation.SAFE_RATIO,
            ("bytes_out",),
            ("out_peers",),
            1.0,
        ),
        NumericFeatureDefinition(
            "interval_mean",
            NumericFeatureFamily.TIMING,
            MeasurementUnit.SECONDS,
            NumericFeatureTransformation.IDENTITY,
            ("interval_mean",),
            missing_value_policy=MissingValuePolicy.POPULATION_MEDIAN_OR_ZERO,
        ),
        NumericFeatureDefinition(
            "interval_cv",
            NumericFeatureFamily.TIMING,
            MeasurementUnit.RATIO,
            NumericFeatureTransformation.IDENTITY,
            ("interval_cv",),
            missing_value_policy=MissingValuePolicy.POPULATION_MEDIAN_OR_ZERO,
        ),
    ),
    timing_evidence=TimingEvidenceRequirement(
        directions=(TimingDirection.OUTBOUND, TimingDirection.INBOUND),
        selection=TimingDirectionSelection.LOWEST_COEFFICIENT_OF_VARIATION,
        minimum_packets_per_direction=MIN_TIMING_PACKETS,
        interval_scope=TimingIntervalScope.WITHIN_CAPTURE,
    ),
)


HOST_DESTINATION_NUMERIC_FEATURE_SET_V1 = NumericFeatureSet(
    feature_set_id="host_destination_numeric",
    version="1",
    features=(
        NumericFeatureDefinition(
            "upload_bytes",
            NumericFeatureFamily.BASE,
            MeasurementUnit.BYTES,
            NumericFeatureTransformation.IDENTITY,
            ("upload_bytes",),
        ),
        NumericFeatureDefinition(
            "upload_packets",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PACKETS,
            NumericFeatureTransformation.IDENTITY,
            ("upload_packets",),
        ),
        NumericFeatureDefinition(
            "download_bytes",
            NumericFeatureFamily.BASE,
            MeasurementUnit.BYTES,
            NumericFeatureTransformation.IDENTITY,
            ("download_bytes",),
        ),
        NumericFeatureDefinition(
            "download_packets",
            NumericFeatureFamily.BASE,
            MeasurementUnit.PACKETS,
            NumericFeatureTransformation.IDENTITY,
            ("download_packets",),
        ),
        NumericFeatureDefinition(
            "upload_download_ratio",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.RATIO,
            NumericFeatureTransformation.SAFE_RATIO,
            ("upload_bytes",),
            ("download_bytes",),
            1.0,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class TextTemplateSpec(VersionedSpec):
    """Versioned text layout independent of provider and model identity."""

    template_id: str = ""
    version: str = "1"
    template: str = ""
    fields: tuple[str, ...] = ()
    missing_value_text: str = "None"
    sequence_format: TextSequenceFormat = TextSequenceFormat.PYTHON_LIST

    def __post_init__(self) -> None:
        template_id = self.template_id.strip()
        version = self.version.strip()
        fields = tuple(value.strip() for value in self.fields)
        if not template_id:
            raise ValueError("text template ID cannot be empty")
        if not version:
            raise ValueError("text template version cannot be empty")
        if not self.template:
            raise ValueError("text template cannot be empty")
        if not fields or any(not value for value in fields):
            raise ValueError("text template fields cannot be empty")
        if len(set(fields)) != len(fields):
            raise ValueError("text template fields must be unique")
        parsed = tuple(Formatter().parse(self.template))
        placeholders = tuple(field_name for _, field_name, _, _ in parsed if field_name)
        if placeholders != fields:
            raise ValueError("text template placeholders must exactly match declared fields")
        if any(
            format_spec or conversion
            for _, field_name, format_spec, conversion in parsed
            if field_name
        ):
            raise ValueError("text template placeholders cannot use formatting or conversions")
        object.__setattr__(self, "template_id", template_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "fields", fields)


ENDPOINT_TEXT_TEMPLATE_V1 = TextTemplateSpec(
    template_id="endpoint-description",
    version="1",
    template=(
        "IP: {ip_address} ({endpoint_type}) | Organization: {organization} | "
        "Hostname: {hostname} | Location: {location}\n"
        "Outbound: {bytes_out} bytes, {packets_out} packets to {out_peers} peers | "
        "Ports: {out_ports}\n"
        "Inbound: {bytes_in} bytes, {packets_in} packets from {in_peers} peers | "
        "Ports: {in_ports}\n"
        "Protocols: {protocols}\n"
    ),
    fields=(
        "ip_address",
        "endpoint_type",
        "organization",
        "hostname",
        "location",
        "bytes_out",
        "packets_out",
        "out_peers",
        "out_ports",
        "bytes_in",
        "packets_in",
        "in_peers",
        "in_ports",
        "protocols",
    ),
    missing_value_text="None",
    sequence_format=TextSequenceFormat.PYTHON_LIST,
)


@dataclass(frozen=True, slots=True)
class RepresentationSpec(VersionedSpec):
    representation_id: str = ""
    version: str = "1"
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.representation_id.strip():
            raise ValueError("representation_id cannot be empty")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ReferenceSpec(VersionedSpec):
    kind: ReferenceKind = ReferenceKind.PEER
    version: str = "1"
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class RankerSpec(VersionedSpec):
    ranker_id: str = ""
    version: str = "1"
    direction: ScoreDirection = ScoreDirection.HIGHER_IS_MORE_ANOMALOUS
    seed: int | None = None
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ranker_id.strip():
            raise ValueError("ranker_id cannot be empty")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ComponentSpec(VersionedSpec):
    """Version-pinned pluggable strategy referenced by an experiment."""

    component_id: str = ""
    version: str = "1"
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        component_id = self.component_id.strip()
        version = self.version.strip()
        if not component_id or not version:
            raise ValueError("component ID and version cannot be empty")
        object.__setattr__(self, "component_id", component_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class HypothesisSpec(VersionedSpec):
    """A falsifiable control/treatment claim with declared decision criteria."""

    schema_version: SchemaVersion = SchemaVersion("2.0.0")
    claim: str = ""
    control: str = ""
    treatments: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    metric_objectives: Parameters = field(default_factory=dict)
    regression_budgets: Parameters = field(default_factory=dict)
    motivated_by_observation_id: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != SchemaVersion("2.0.0"):
            raise ValueError(f"unsupported hypothesis schema {self.schema_version}; expected 2.0.0")
        claim = self.claim.strip()
        control = self.control.strip()
        treatments = tuple(item.strip() for item in self.treatments)
        metrics = tuple(item.strip() for item in self.metrics)
        if not claim or not control:
            raise ValueError("hypothesis requires a claim and control")
        if not treatments or any(not item for item in treatments):
            raise ValueError("hypothesis requires at least one treatment")
        if len(set(treatments)) != len(treatments) or control in treatments:
            raise ValueError("hypothesis control and treatments must be distinct")
        if not metrics or any(not item for item in metrics) or len(set(metrics)) != len(metrics):
            raise ValueError("hypothesis metrics must be nonempty and unique")
        if any(not str(metric).strip() for metric in self.regression_budgets):
            raise ValueError("regression budget metric names cannot be empty")
        budgets = _immutable_mapping(
            {str(metric).strip(): budget for metric, budget in self.regression_budgets.items()}
        )
        objectives: dict[str, MetricObjective] = {}
        for raw_metric, raw_objective in self.metric_objectives.items():
            metric = str(raw_metric).strip()
            if not metric:
                raise ValueError("metric objective names cannot be empty")
            try:
                objectives[metric] = MetricObjective(str(raw_objective))
            except ValueError as error:
                raise ValueError(
                    f"metric objective for {metric!r} must be 'maximize' or 'minimize'"
                ) from error
        decision_metrics = set(metrics) | {str(metric).strip() for metric in budgets}
        missing = decision_metrics - set(objectives)
        unused = set(objectives) - decision_metrics
        if missing or unused:
            raise ValueError(
                "metric objectives must exactly cover primary and regression metrics; "
                f"missing={sorted(missing)}, unused={sorted(unused)}"
            )
        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "control", control)
        object.__setattr__(self, "treatments", treatments)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "metric_objectives", MappingProxyType(objectives))
        object.__setattr__(self, "regression_budgets", budgets)


@dataclass(frozen=True, slots=True)
class ExperimentSpec(VersionedSpec):
    hypothesis: str | HypothesisSpec = ""
    observation: ObservationWindow | None = None
    entity: EntityDefinition = field(default_factory=EntityDefinition)
    representation: RepresentationSpec | None = None
    reference: ReferenceSpec = field(default_factory=ReferenceSpec)
    ranker: RankerSpec | None = None
    evaluator: ComponentSpec = field(
        default_factory=lambda: ComponentSpec(component_id="standard_metrics")
    )
    renderer: ComponentSpec = field(default_factory=lambda: ComponentSpec(component_id="json"))
    dataset_ids: tuple[DatasetId, ...] = ()
    evidence_digests: Parameters = field(default_factory=dict)
    label_source_versions: Parameters = field(default_factory=dict)
    deterministic_settings: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.hypothesis, str) and not self.hypothesis.strip():
            raise ValueError("experiment hypothesis cannot be empty")
        if self.observation is None or self.representation is None or self.ranker is None:
            raise ValueError("experiment requires observation, representation, and ranker specs")
        if len(set(self.dataset_ids)) != len(self.dataset_ids):
            raise ValueError("experiment dataset IDs must be unique")
        evidence_digests = _immutable_mapping(self.evidence_digests)
        if any(
            len(str(digest)) != 64
            or any(character not in "0123456789abcdef" for character in str(digest))
            for digest in evidence_digests.values()
        ):
            raise ValueError("evidence digests must be lowercase SHA-256 text")
        object.__setattr__(self, "dataset_ids", tuple(self.dataset_ids))
        object.__setattr__(self, "evidence_digests", evidence_digests)
        object.__setattr__(
            self, "label_source_versions", _immutable_mapping(self.label_source_versions)
        )
        object.__setattr__(
            self, "deterministic_settings", _immutable_mapping(self.deterministic_settings)
        )

    @property
    def experiment_id(self) -> ExperimentId:
        return ExperimentId(str(self.digest))


@dataclass(frozen=True, slots=True)
class EvidencePointer(VersionedSpec):
    capture_id: CaptureId | None = None
    entity_id: EntityId | None = None
    artifact_digest: CanonicalDigest | None = None
    selector: str | None = None

    def __post_init__(self) -> None:
        if self.capture_id is None and self.artifact_digest is None:
            raise ValueError("evidence pointer requires capture or artifact identity")


@dataclass(frozen=True, slots=True)
class Score:
    value: float
    direction: ScoreDirection

    def __post_init__(self) -> None:
        if not float("-inf") < self.value < float("inf"):
            raise ValueError("score must be finite")


@dataclass(frozen=True, slots=True)
class RankedFinding(VersionedSpec):
    finding_id: FindingId | None = None
    entity_id: EntityId | None = None
    rank: int = 0
    score: Score | None = None
    outlier: OutlierStatus = OutlierStatus.NOT_SCORED
    evidence: tuple[EvidencePointer, ...] = ()

    def __post_init__(self) -> None:
        if self.finding_id is None or self.entity_id is None or self.score is None:
            raise ValueError("finding requires finding, entity, and score identities")
        if self.rank < 1:
            raise ValueError("rank must be a positive one-based integer")
