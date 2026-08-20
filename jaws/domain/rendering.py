"""Retained, renderer-neutral visualization inputs."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PortSizePlotData:
    rows: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        if any(
            size < 0 or source < 0 or destination < 0 for size, source, destination in self.rows
        ):
            raise ValueError("port-size plot values cannot be negative")


@dataclass(frozen=True, slots=True)
class KDistancePlotData:
    distances: tuple[float, ...]

    def __post_init__(self) -> None:
        if any(not isfinite(value) or value < 0 for value in self.distances):
            raise ValueError("k-distance plot values must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ClusterPlotData:
    coordinates: tuple[tuple[float, float], ...]
    labels: tuple[int, ...]
    annotations: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        if len(self.coordinates) != len(self.labels) or len(self.labels) != len(self.annotations):
            raise ValueError("cluster plot inputs must align")
        if any(not isfinite(value) for point in self.coordinates for value in point):
            raise ValueError("cluster coordinates must be finite")
