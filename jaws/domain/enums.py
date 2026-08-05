"""Stable domain enumerations."""

from enum import StrEnum


class CaptureState(StrEnum):
    REGISTERED = "registered"
    RUNNING = "running"
    IMPORTING = "importing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class ScoreDirection(StrEnum):
    HIGHER_IS_MORE_ANOMALOUS = "higher_is_more_anomalous"
    LOWER_IS_MORE_ANOMALOUS = "lower_is_more_anomalous"


class OutlierStatus(StrEnum):
    OUTLIER = "outlier"
    INLIER = "inlier"
    NOT_SCORED = "not_scored"


class MeasurementUnit(StrEnum):
    BYTES = "bytes"
    PACKETS = "packets"
    SECONDS = "seconds"
    MILLISECONDS = "milliseconds"
    RATIO = "ratio"
    RANK = "rank"
    SCORE = "score"


class ErrorCategory(StrEnum):
    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    UNAVAILABLE_DEPENDENCY = "unavailable_dependency"
    STORAGE = "storage"
    PROVIDER = "provider"
    CAPTURE = "capture"
    EXPERIMENT = "experiment"
    INTERNAL = "internal"
