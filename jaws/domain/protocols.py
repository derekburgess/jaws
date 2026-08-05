"""Injectable standard-library-only ports for deterministic domain work."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, TypeVar

from .identifiers import Identifier

IdentifierT = TypeVar("IdentifierT", bound=Identifier, covariant=True)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol[IdentifierT]):
    def new(self) -> IdentifierT: ...
