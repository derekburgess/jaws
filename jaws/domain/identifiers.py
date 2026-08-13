"""Runtime-distinct identifiers used by research-domain records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType, Self

CanonicalDigest = NewType("CanonicalDigest", str)
SchemaVersion = NewType("SchemaVersion", str)


@dataclass(frozen=True, slots=True, order=True)
class Identifier:
    """Validated string identity whose subclasses remain distinct at runtime."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip()
        if not normalized:
            raise ValueError(f"{type(self).__name__} cannot be empty")
        object.__setattr__(self, "value", normalized)

    @classmethod
    def parse(cls, value: str) -> Self:
        return cls(value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class DatasetId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class CaptureId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class ObservationScopeId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class EntityId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class ExperimentId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class RunId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class FindingId(Identifier):
    pass


@dataclass(frozen=True, slots=True, order=True)
class AuditEventId(Identifier):
    pass
