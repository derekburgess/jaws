"""Stable domain enumerations."""

from enum import StrEnum
from typing import Self


class CaptureState(StrEnum):
    REGISTERED = "registered"
    RUNNING = "running"
    IMPORTING = "importing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CaptureSourceKind(StrEnum):
    LIVE_INTERFACE = "live_interface"
    PCAP_FILE = "pcap_file"
    LEGACY_UNKNOWN = "legacy_unknown"


class ObservationScopeKind(StrEnum):
    CAPTURE = "capture"
    POOLED = "pooled"
    CUSTOM = "custom"
    LEGACY = "legacy"


class EnrichmentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    NOT_APPLICABLE = "not_applicable"
    NOT_FOUND = "not_found"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"


class ProfileStatus(StrEnum):
    CURRENT = "current"
    LEGACY_UNVERSIONED = "legacy_unversioned"
    LEGACY_QUARANTINED = "legacy_quarantined"


class RunState(StrEnum):
    PLANNED = "planned"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    # Source-compatibility aliases for the pre-Milestone-5 vocabulary. New
    # serialized records always use planned/completed.
    CREATED = "planned"
    SUCCEEDED = "completed"

    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        if value == "created":
            return cls.PLANNED
        if value == "succeeded":
            return cls.COMPLETED
        return None


class EntityType(StrEnum):
    ENDPOINT_IP = "endpoint_ip"
    HOST_DESTINATION = "host_destination"
    FLOW = "flow"
    SERVICE = "service"
    SUBNET = "subnet"


class ReferenceKind(StrEnum):
    PEER = "peer"
    HISTORICAL = "historical"
    HYBRID = "hybrid"
    RESEARCHER_DEFINED = "researcher_defined"


class ReferenceEligibility(StrEnum):
    """Why an entity is using a particular comparison frame."""

    PEER = "peer"
    HISTORICAL = "historical"
    FIRST_SEEN = "first_seen"
    INSUFFICIENT_HISTORY = "insufficient_history"
    EXCLUDED = "excluded"


class ComparisonFrame(StrEnum):
    PEER = "peer"
    OWN_HISTORY = "own_history"


class ScoreDirection(StrEnum):
    HIGHER_IS_MORE_ANOMALOUS = "higher_is_more_anomalous"
    LOWER_IS_MORE_ANOMALOUS = "lower_is_more_anomalous"


class MetricObjective(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class OutlierStatus(StrEnum):
    OUTLIER = "outlier"
    INLIER = "inlier"
    NOT_SCORED = "not_scored"


class MeasurementUnit(StrEnum):
    BYTES = "bytes"
    PACKETS = "packets"
    PEERS = "peers"
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"
    RATIO = "ratio"
    BYTES_PER_PACKET = "bytes/packet"
    BYTES_PER_PEER = "bytes/peer"
    RANK = "rank"
    SCORE = "score"


class NumericFeatureFamily(StrEnum):
    BASE = "base"
    SHAPE = "shape"
    TIMING = "timing"


class NumericFeatureTransformation(StrEnum):
    IDENTITY = "identity"
    SAFE_RATIO = "safe_ratio"


class NumericAnalysisTransformation(StrEnum):
    LOG1P = "log1p"


class MissingValuePolicy(StrEnum):
    FORBID = "forbid"
    POPULATION_MEDIAN_OR_ZERO = "population_median_or_zero"


class EmbeddingNormalization(StrEnum):
    NONE = "none"
    L2 = "l2"


class TimingDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class TimingDirectionSelection(StrEnum):
    LOWEST_COEFFICIENT_OF_VARIATION = "lowest_coefficient_of_variation"


class TimingIntervalScope(StrEnum):
    WITHIN_CAPTURE = "within_capture"


class TextSequenceFormat(StrEnum):
    PYTHON_LIST = "python_list"


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    UNAVAILABLE_DEPENDENCY = "unavailable_dependency"
    STORAGE = "storage"
    PROVIDER = "provider"
    CAPTURE = "capture"
    EXPERIMENT = "experiment"
    INTERNAL = "internal"
