"""Shared behavior for in-memory and Neo4j experiment/run indexes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CanonicalDigest,
    ExperimentId,
    ExperimentRunIndex,
    RunId,
    RunState,
)
from jaws.ports import DuplicateRunError, RunNotFoundError, RunStateConflictError

SPECIFICATION_DIGEST = CanonicalDigest("a" * 64)
EXPERIMENT_ID = ExperimentId(str(SPECIFICATION_DIGEST))
ARTIFACT_DIGEST = CanonicalDigest("b" * 64)


def assert_experiment_index_repository_contract(repository):
    """Exercise append-only identity, lifecycle, supersession, and discovery."""

    created_at = datetime(2026, 8, 18, 14, tzinfo=UTC)
    first = ExperimentRunIndex(
        run_id=RunId("run_experiment_index_first"),
        experiment_id=EXPERIMENT_ID,
        specification_digest=SPECIFICATION_DIGEST,
        state=RunState.CREATED,
        created_at=created_at,
    )
    repository.add(first)
    with pytest.raises(DuplicateRunError):
        repository.add(first)
    assert repository.get(first.run_id) == first
    assert repository.get(RunId("run_experiment_index_missing")) is None

    premature_rerun = replace(
        first,
        run_id=RunId("run_experiment_index_premature"),
        created_at=created_at + timedelta(seconds=1),
        supersedes_run_id=first.run_id,
    )
    with pytest.raises(ValueError, match="terminal run"):
        repository.add(premature_rerun)

    altered_running = replace(
        first.transition(RunState.RUNNING, created_at + timedelta(seconds=2)),
        created_at=created_at - timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="identity metadata"):
        repository.transition(altered_running, expected_state=RunState.CREATED)

    running = first.transition(RunState.RUNNING, created_at + timedelta(seconds=2))
    repository.transition(running, expected_state=RunState.CREATED)
    with pytest.raises(RunStateConflictError):
        repository.transition(running, expected_state=RunState.CREATED)
    with pytest.raises(RunNotFoundError):
        repository.transition(
            replace(running, run_id=RunId("run_experiment_index_missing")),
            expected_state=RunState.RUNNING,
        )

    succeeded = running.transition(
        RunState.SUCCEEDED,
        created_at + timedelta(seconds=4),
        artifact_uri="artifact://runs/first.json",
        artifact_digest=ARTIFACT_DIGEST,
    )
    repository.transition(succeeded, expected_state=RunState.RUNNING)
    assert repository.get(first.run_id) == succeeded

    second = ExperimentRunIndex(
        run_id=RunId("run_experiment_index_second"),
        experiment_id=EXPERIMENT_ID,
        specification_digest=SPECIFICATION_DIGEST,
        state=RunState.CREATED,
        created_at=created_at + timedelta(seconds=5),
        supersedes_run_id=first.run_id,
    )
    repository.add(second)
    assert repository.list_for_experiment(EXPERIMENT_ID) == (succeeded, second)
    assert repository.list_all() == (succeeded, second)

    other_digest = CanonicalDigest("c" * 64)
    other = ExperimentRunIndex(
        run_id=RunId("run_experiment_index_other"),
        experiment_id=ExperimentId(str(other_digest)),
        specification_digest=other_digest,
        state=RunState.CREATED,
        created_at=created_at + timedelta(seconds=6),
        supersedes_run_id=first.run_id,
    )
    with pytest.raises(ValueError, match="same experiment"):
        repository.add(other)

    missing_predecessor = replace(
        other,
        supersedes_run_id=RunId("run_experiment_index_missing"),
    )
    with pytest.raises(RunNotFoundError):
        repository.add(missing_predecessor)
