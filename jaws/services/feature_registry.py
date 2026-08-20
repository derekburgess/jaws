"""Versioned numeric feature registry shared by every research surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    HOST_DESTINATION_NUMERIC_FEATURE_SET_V1,
    MeasurementUnit,
    MissingValuePolicy,
    NumericAnalysisTransformation,
    NumericFeatureSet,
    NumericFeatureTransformation,
)


@dataclass(frozen=True, slots=True)
class HostFlowMetadata:
    feature: str
    endpoint_flow: str | None
    local_interpretation: str | None
    remote_interpretation: str | None


@dataclass(frozen=True, slots=True)
class FeatureMetadata:
    name: str
    unit: MeasurementUnit
    direction: str
    transformation: str
    required_evidence: tuple[str, ...]
    interpretation: str
    host_flow: HostFlowMetadata | None = None


_ENDPOINT_FLOW = {
    "bytes_out": "out",
    "packets_out": "out",
    "bytes_in": "in",
    "packets_in": "in",
    "bytes_out_in_ratio": "out",
    "packets_out_in_ratio": "out",
    "bytes_per_peer": "out",
}
_LOCAL = {
    "out": "host → network: traffic the capture host sent out (outbound-from-host signal)",
    "in": "network → host: traffic the capture host received (download)",
}
_REMOTE = {
    "out": "remote → host: traffic the capture host downloaded (NOT host exfil)",
    "in": "host → remote: traffic the capture host sent to this IP (outbound-from-host signal)",
}
_HOST_DESTINATION = {
    "upload_bytes": "host → remote: data the capture host sent out (outbound from host)",
    "upload_packets": "host → remote: packets the capture host sent out (outbound from host)",
    "download_bytes": "remote → host: data the capture host received",
    "download_packets": "remote → host: packets the capture host received",
    "upload_download_ratio": "host sent more to this peer than it received (exfil-shaped when high)",
}


class NumericFeatureRegistry:
    """Render and validate a declared ordered numeric representation."""

    def __init__(self, feature_set: NumericFeatureSet) -> None:
        self.feature_set = feature_set

    @property
    def metadata(self) -> tuple[FeatureMetadata, ...]:
        rows = []
        for feature in self.feature_set.features:
            flow = _ENDPOINT_FLOW.get(feature.name)
            host_flow = None
            if flow is not None:
                host_flow = HostFlowMetadata(feature.name, flow, _LOCAL[flow], _REMOTE[flow])
            elif feature.name in _HOST_DESTINATION:
                host_flow = HostFlowMetadata(
                    feature.name, "host", _HOST_DESTINATION[feature.name], _HOST_DESTINATION[feature.name]
                )
            rows.append(
                FeatureMetadata(
                    name=feature.name,
                    unit=feature.unit,
                    direction="low" if feature.name == "interval_cv" else "high",
                    transformation=(
                        f"{feature.analysis_transformation.value}:{feature.transformation.value}"
                    ),
                    required_evidence=feature.numerator_fields + feature.denominator_fields,
                    interpretation=cast(
                        str,
                        _HOST_DESTINATION.get(feature.name)
                        or (host_flow.remote_interpretation if host_flow else feature.name),
                    ),
                    host_flow=host_flow,
                )
            )
        return tuple(rows)

    def raw_matrix(self, rows: Sequence[Mapping[str, object]]) -> np.ndarray:
        columns: list[list[float | None]] = []
        for feature in self.feature_set.features:
            column: list[float | None] = []
            sources = feature.numerator_fields + feature.denominator_fields
            for row in rows:
                values = [row.get(name) for name in sources]
                if any(value is None for value in values):
                    if feature.missing_value_policy is MissingValuePolicy.FORBID:
                        raise ValueError(f"numeric feature {feature.name} forbids missing values")
                    column.append(None)
                    continue
                numeric = [float(cast(float | int | str, value)) for value in values]
                numerator_count = len(feature.numerator_fields)
                numerator = sum(numeric[:numerator_count])
                if feature.transformation is NumericFeatureTransformation.IDENTITY:
                    value = numerator
                elif feature.transformation is NumericFeatureTransformation.SAFE_RATIO:
                    value = numerator / (
                        sum(numeric[numerator_count:]) + feature.denominator_offset
                    )
                else:  # pragma: no cover - closed enum
                    raise ValueError(f"unsupported numeric transformation: {feature.transformation}")
                if not np.isfinite(value) or value < 0:
                    raise ValueError(f"numeric feature {feature.name} must be finite and non-negative")
                column.append(value)
            if any(value is None for value in column):
                present = [value for value in column if value is not None]
                replacement = float(np.median(present)) if present else 0.0
                column = [replacement if value is None else value for value in column]
            columns.append(column)
        if not rows:
            return np.empty((0, len(self.feature_set.features)))
        matrix = np.asarray(columns, dtype=float).T
        self.validate_matrix(matrix)
        return matrix

    def transform(self, raw: np.ndarray) -> np.ndarray:
        self.validate_matrix(raw)
        transformed = np.empty_like(raw, dtype=float)
        for column, feature in enumerate(self.feature_set.features):
            if feature.analysis_transformation is NumericAnalysisTransformation.LOG1P:
                transformed[:, column] = np.log1p(raw[:, column])
            else:  # pragma: no cover - closed enum
                raise ValueError(
                    f"unsupported analysis transformation: {feature.analysis_transformation}"
                )
        if not np.all(np.isfinite(transformed)):
            raise ValueError("numeric representation contains non-finite transformed values")
        return transformed

    def validate_matrix(self, matrix: np.ndarray) -> None:
        expected = len(self.feature_set.features)
        if matrix.ndim != 2 or matrix.shape[1] != expected:
            raise ValueError(
                f"numeric matrix does not match {self.feature_set.feature_set_id}@"
                f"{self.feature_set.version}: expected {expected} columns"
            )
        if not np.all(np.isfinite(matrix)):
            raise ValueError("numeric representation contains non-finite values")

    def host_relative_gloss(self, feature: str, *, is_local: bool) -> str | None:
        metadata = next((row for row in self.metadata if row.name == feature), None)
        if metadata is None or metadata.host_flow is None:
            return None
        return (
            metadata.host_flow.local_interpretation
            if is_local
            else metadata.host_flow.remote_interpretation
        )


ENDPOINT_FEATURE_REGISTRY_V1 = NumericFeatureRegistry(ENDPOINT_NUMERIC_FEATURE_SET_V1)
HOST_DESTINATION_FEATURE_REGISTRY_V1 = NumericFeatureRegistry(
    HOST_DESTINATION_NUMERIC_FEATURE_SET_V1
)
