"""Typed service envelopes and legacy flat-envelope compatibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from .errors import DomainError
from .identifiers import SchemaVersion
from .serialization import primitive

ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class Success(Generic[ResultT]):
    data: ResultT
    schema_version: SchemaVersion = SchemaVersion("1.0.0")

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Failure:
    error: DomainError
    schema_version: SchemaVersion = SchemaVersion("1.0.0")

    @property
    def ok(self) -> bool:
        return False


Result = Success[ResultT] | Failure


def structured_envelope(result: Result[Any]) -> dict[str, Any]:
    """Modern nested envelope used by callable service boundaries."""
    if isinstance(result, Success):
        return {
            "schema_version": str(result.schema_version),
            "ok": True,
            "data": primitive(result.data),
        }
    return {
        "schema_version": str(result.schema_version),
        "ok": False,
        "error": primitive(result.error),
    }


def legacy_success(data: dict[str, Any]) -> dict[str, Any]:
    """Preserve the existing flat CLI/MCP payload, including key order."""
    if "ok" in data:
        raise ValueError("legacy result data cannot define reserved key 'ok'")
    return {"ok": True, **data}


def legacy_failure(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **details}
