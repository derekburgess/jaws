"""Credential values that cannot leak through reprs, logs, or provenance."""

from __future__ import annotations

from typing import Final

REDACTED: Final = "***redacted***"
UNSET: Final = "***unset***"


class Secret:
    """A credential that never renders its own value.

    Deliberately NOT a dataclass. `jaws.domain.serialization.primitive` walks dataclass
    fields, so a dataclass secret would serialize the very value it is meant to hide;
    this class exposes no fields to walk and `primitive` redacts it explicitly. `__str__`
    is `__repr__` so that an f-string — the most likely accidental leak — is safe too.

    Reading the value is always an explicit call: `reveal` for optional access, `require`
    for the invoke-time check that turns a missing credential into a usable message.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str | None = None) -> None:
        cleaned = value.strip() if value is not None else None
        self._value = cleaned or None

    @property
    def configured(self) -> bool:
        return self._value is not None

    def reveal(self) -> str | None:
        return self._value

    def require(self, name: str, guidance: str = "") -> str:
        if self._value is None:
            raise ValueError(f"{name} is not set. {guidance}".strip())
        return self._value

    def __repr__(self) -> str:
        return f"Secret({REDACTED if self.configured else UNSET})"

    def __str__(self) -> str:
        return self.__repr__()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((Secret, self._value))

    def __bool__(self) -> bool:
        return self.configured
