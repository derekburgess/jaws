"""Deterministic in-memory experiment/run index repository."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from jaws.domain import (
    RUN_TRANSITIONS,
    TERMINAL_RUN_STATES,
    CanonicalDigest,
    ExperimentId,
    ExperimentRunIndex,
    RunId,
    RunState,
    experiment_run_metadata,
    require_transition,
)

from .repositories import (
    DuplicateRunError,
    ExperimentDigestConflictError,
    RunNotFoundError,
    RunStateConflictError,
)


@dataclass(slots=True)
class InMemoryExperimentIndexRepository:
    """Append-only identities with optimistic lifecycle transitions."""

    _runs: dict[RunId, ExperimentRunIndex] = field(default_factory=dict)
    _digests: dict[ExperimentId, CanonicalDigest] = field(default_factory=dict)

    def add(self, record: ExperimentRunIndex) -> None:
        if record.state is not RunState.CREATED:
            raise ValueError("new experiment run index must start in created state")
        if record.run_id in self._runs:
            raise DuplicateRunError(f"experiment run already exists: {record.run_id}")
        existing_digest = self._digests.get(record.experiment_id)
        if existing_digest is not None and existing_digest != record.specification_digest:
            raise ExperimentDigestConflictError(
                f"experiment digest does not match existing index: {record.experiment_id}"
            )
        if record.supersedes_run_id is not None:
            superseded = self._runs.get(record.supersedes_run_id)
            if superseded is None:
                raise RunNotFoundError(
                    f"superseded experiment run does not exist: {record.supersedes_run_id}"
                )
            if superseded.experiment_id != record.experiment_id:
                raise ValueError(
                    "an experiment run may supersede only a run of the same experiment"
                )
            if superseded.state not in TERMINAL_RUN_STATES:
                raise ValueError("an experiment run may supersede only a terminal run")
        self._digests[record.experiment_id] = record.specification_digest
        self._runs[record.run_id] = record

    def get(self, run_id: RunId) -> ExperimentRunIndex | None:
        return self._runs.get(run_id)

    def list_for_experiment(self, experiment_id: ExperimentId) -> tuple[ExperimentRunIndex, ...]:
        return self._ordered(
            record for record in self._runs.values() if record.experiment_id == experiment_id
        )

    def list_all(self) -> tuple[ExperimentRunIndex, ...]:
        return self._ordered(self._runs.values())

    def transition(self, record: ExperimentRunIndex, *, expected_state: RunState) -> None:
        existing = self.get(record.run_id)
        if existing is None:
            raise RunNotFoundError(f"experiment run does not exist: {record.run_id}")
        if existing.state is not expected_state:
            raise RunStateConflictError(
                f"experiment run {record.run_id} is {existing.state}, expected {expected_state}"
            )
        require_transition(expected_state, record.state, RUN_TRANSITIONS)
        if experiment_run_metadata(existing) != experiment_run_metadata(record):
            raise ValueError("experiment run identity metadata cannot change during transition")
        self._runs[record.run_id] = record

    @staticmethod
    def _ordered(records: Iterable[ExperimentRunIndex]) -> tuple[ExperimentRunIndex, ...]:
        return tuple(sorted(records, key=lambda record: (record.created_at, record.run_id.value)))
