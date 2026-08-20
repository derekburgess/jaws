"""Minimal experiment/run discovery indexes for canonical external artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import urlsplit

from .enums import RunState
from .identifiers import CanonicalDigest, ExperimentId, RunId
from .time import RUN_TRANSITIONS, normalize_utc, require_transition

TERMINAL_RUN_STATES = frozenset(
    {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED, RunState.SUPERSEDED}
)


def _sha256(value: CanonicalDigest, field_name: str) -> None:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field_name} must be lowercase SHA-256 text")


def experiment_run_metadata(record: ExperimentRunIndex) -> tuple[object, ...]:
    """Return identity fields that a lifecycle transition may never rewrite."""

    return (
        record.run_id,
        record.experiment_id,
        record.specification_digest,
        record.created_at,
        record.supersedes_run_id,
    )


@dataclass(frozen=True, slots=True)
class ExperimentRunIndex:
    """One graph-discovery snapshot pointing at a canonical external run artifact."""

    run_id: RunId
    experiment_id: ExperimentId
    specification_digest: CanonicalDigest
    state: RunState
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    artifact_uri: str | None = None
    artifact_digest: CanonicalDigest | None = None
    failure_code: str | None = None
    supersedes_run_id: RunId | None = None

    def __post_init__(self) -> None:
        _sha256(self.specification_digest, "specification_digest")
        if self.experiment_id.value != str(self.specification_digest):
            raise ValueError("experiment_id must equal the canonical specification digest")
        object.__setattr__(self, "created_at", normalize_utc(self.created_at))
        for field_name in ("started_at", "ended_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, normalize_utc(value))
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("experiment run started before it was created")
        lower_bound = self.started_at or self.created_at
        if self.ended_at is not None and self.ended_at < lower_bound:
            raise ValueError("experiment run ended before it started")
        if self.state is RunState.CREATED and (self.started_at or self.ended_at):
            raise ValueError("created experiment run cannot have execution timestamps")
        if self.state is RunState.RUNNING and (self.started_at is None or self.ended_at):
            raise ValueError("running experiment run requires only started_at")
        if self.state in TERMINAL_RUN_STATES and self.ended_at is None:
            raise ValueError("terminal experiment run requires ended_at")

        artifact_uri = self.artifact_uri.strip() if self.artifact_uri else None
        if (artifact_uri is None) != (self.artifact_digest is None):
            raise ValueError("artifact URI and digest must be provided together")
        if artifact_uri is not None:
            parsed = urlsplit(artifact_uri)
            if not parsed.scheme:
                raise ValueError("artifact URI must include a scheme")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "artifact URI cannot contain credentials, query parameters, or fragments"
                )
            assert self.artifact_digest is not None
            _sha256(self.artifact_digest, "artifact_digest")
            if self.state not in TERMINAL_RUN_STATES:
                raise ValueError("only terminal experiment runs may publish artifacts")
        if self.state is RunState.SUCCEEDED and artifact_uri is None:
            raise ValueError("successful experiment run requires a canonical artifact")
        object.__setattr__(self, "artifact_uri", artifact_uri)

        failure_code = self.failure_code.strip() if self.failure_code else None
        if self.state is RunState.FAILED and failure_code is None:
            raise ValueError("failed experiment run requires failure_code")
        if self.state is not RunState.FAILED and failure_code is not None:
            raise ValueError("only failed experiment runs may have failure_code")
        object.__setattr__(self, "failure_code", failure_code)
        if self.supersedes_run_id == self.run_id:
            raise ValueError("experiment run cannot supersede itself")

    def transition(
        self,
        target: RunState,
        at: datetime,
        *,
        artifact_uri: str | None = None,
        artifact_digest: CanonicalDigest | None = None,
        failure_code: str | None = None,
    ) -> ExperimentRunIndex:
        """Return the next validated lifecycle snapshot."""

        require_transition(self.state, target, RUN_TRANSITIONS)
        timestamp = normalize_utc(at)
        return replace(
            self,
            state=target,
            started_at=timestamp if target is RunState.RUNNING else self.started_at,
            ended_at=timestamp if target in TERMINAL_RUN_STATES else self.ended_at,
            artifact_uri=artifact_uri,
            artifact_digest=artifact_digest,
            failure_code=failure_code,
        )
