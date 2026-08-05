"""Stable machine-readable failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .enums import ErrorCategory

ErrorDetails = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DomainError:
    category: ErrorCategory
    code: str
    message: str
    details: ErrorDetails = field(default_factory=dict)
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("error code and message cannot be empty")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
