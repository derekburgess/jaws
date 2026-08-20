"""Canonical time and lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from .enums import CaptureState, RunState


def normalize_utc(value: datetime) -> datetime:
    """Return an aware UTC timestamp, rejecting ambiguous naive values."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return normalize_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


CAPTURE_TRANSITIONS: dict[CaptureState, frozenset[CaptureState]] = {
    CaptureState.REGISTERED: frozenset(
        {CaptureState.RUNNING, CaptureState.IMPORTING, CaptureState.CANCELLED}
    ),
    CaptureState.RUNNING: frozenset(
        {CaptureState.COMPLETE, CaptureState.PARTIAL, CaptureState.FAILED, CaptureState.CANCELLED}
    ),
    CaptureState.IMPORTING: frozenset(
        {CaptureState.COMPLETE, CaptureState.PARTIAL, CaptureState.FAILED, CaptureState.CANCELLED}
    ),
    CaptureState.COMPLETE: frozenset(),
    CaptureState.PARTIAL: frozenset(),
    CaptureState.FAILED: frozenset(),
    CaptureState.CANCELLED: frozenset(),
}

RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    # Direct planned -> running remains accepted for pre-Milestone-5 clients;
    # the research runner itself always records the queued state.
    RunState.PLANNED: frozenset({RunState.QUEUED, RunState.RUNNING, RunState.CANCELLED}),
    RunState.QUEUED: frozenset({RunState.RUNNING, RunState.CANCELLED}),
    RunState.RUNNING: frozenset(
        {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}
    ),
    RunState.COMPLETED: frozenset({RunState.SUPERSEDED}),
    RunState.FAILED: frozenset({RunState.SUPERSEDED}),
    RunState.CANCELLED: frozenset({RunState.SUPERSEDED}),
    RunState.SUPERSEDED: frozenset(),
}


def require_transition[StateT](
    current: StateT, target: StateT, allowed: dict[StateT, frozenset[StateT]]
) -> StateT:
    if target not in allowed[current]:
        raise ValueError(f"invalid lifecycle transition: {current} -> {target}")
    return target
