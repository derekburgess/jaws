"""Values whose unit is explicit at every boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import MeasurementUnit


@dataclass(frozen=True, slots=True)
class Measurement:
    value: int | float
    unit: MeasurementUnit

    def __post_init__(self) -> None:
        if not float("-inf") < float(self.value) < float("inf"):
            raise ValueError("measurement must be finite")


def bytes_count(value: int) -> Measurement:
    if value < 0:
        raise ValueError("byte count cannot be negative")
    return Measurement(value, MeasurementUnit.BYTES)


def packet_count(value: int) -> Measurement:
    if value < 0:
        raise ValueError("packet count cannot be negative")
    return Measurement(value, MeasurementUnit.PACKETS)


def interval_seconds(value: float) -> Measurement:
    if value < 0:
        raise ValueError("interval cannot be negative")
    return Measurement(value, MeasurementUnit.SECONDS)


def ratio(value: float) -> Measurement:
    if not 0.0 <= value <= 1.0:
        raise ValueError("ratio must be between zero and one")
    return Measurement(value, MeasurementUnit.RATIO)
