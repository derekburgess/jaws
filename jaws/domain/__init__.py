"""Lightweight public contracts for the JAWS research workbench."""

from .enums import (
    CaptureState,
    EntityType,
    ErrorCategory,
    MeasurementUnit,
    OutlierStatus,
    ReferenceKind,
    RunState,
    ScoreDirection,
)
from .errors import DomainError
from .identifiers import (
    CanonicalDigest,
    CaptureId,
    DatasetId,
    EntityId,
    ExperimentId,
    FindingId,
    RunId,
    SchemaVersion,
)
from .measurements import Measurement, bytes_count, interval_seconds, packet_count, ratio
from .protocols import Clock, IdGenerator
from .ranking import ScoredEntity, deterministic_ranking
from .results import Failure, Result, Success, legacy_failure, legacy_success, structured_envelope
from .secrets import REDACTED, UNSET, Secret
from .serialization import canonical_digest, canonical_json, primitive
from .specifications import (
    EntityDefinition,
    EvidencePointer,
    ExperimentSpec,
    ObservationWindow,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
    RepresentationSpec,
    Score,
    VersionedSpec,
)
from .time import CAPTURE_TRANSITIONS, RUN_TRANSITIONS, normalize_utc, require_transition, utc_text

__all__ = [
    "CAPTURE_TRANSITIONS",
    "REDACTED",
    "RUN_TRANSITIONS",
    "UNSET",
    "CanonicalDigest",
    "CaptureId",
    "CaptureState",
    "Clock",
    "DatasetId",
    "DomainError",
    "EntityDefinition",
    "EntityId",
    "EntityType",
    "ErrorCategory",
    "EvidencePointer",
    "ExperimentId",
    "ExperimentSpec",
    "Failure",
    "FindingId",
    "IdGenerator",
    "Measurement",
    "MeasurementUnit",
    "ObservationWindow",
    "OutlierStatus",
    "RankedFinding",
    "RankerSpec",
    "ReferenceKind",
    "ReferenceSpec",
    "RepresentationSpec",
    "Result",
    "RunId",
    "RunState",
    "SchemaVersion",
    "Score",
    "ScoreDirection",
    "ScoredEntity",
    "Secret",
    "Success",
    "VersionedSpec",
    "bytes_count",
    "canonical_digest",
    "canonical_json",
    "deterministic_ranking",
    "interval_seconds",
    "legacy_failure",
    "legacy_success",
    "normalize_utc",
    "packet_count",
    "primitive",
    "ratio",
    "require_transition",
    "structured_envelope",
    "utc_text",
]
