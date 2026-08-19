"""Offline experiment/run index domain and repository contracts."""

from datetime import UTC, datetime, timedelta

import pytest
from experiment_index_repository_contract import (
    ARTIFACT_DIGEST,
    EXPERIMENT_ID,
    SPECIFICATION_DIGEST,
    assert_experiment_index_repository_contract,
)

from jaws.domain import ExperimentRunIndex, RunId, RunState
from jaws.ports import InMemoryExperimentIndexRepository


def test_in_memory_experiment_index_repository_follows_contract():
    assert_experiment_index_repository_contract(InMemoryExperimentIndexRepository())


def test_experiment_run_index_rejects_noncanonical_or_secret_artifact_metadata():
    created_at = datetime(2026, 8, 18, 14, tzinfo=UTC)
    created = ExperimentRunIndex(
        run_id=RunId("run-domain-contract"),
        experiment_id=EXPERIMENT_ID,
        specification_digest=SPECIFICATION_DIGEST,
        state=RunState.CREATED,
        created_at=created_at,
    )
    running = created.transition(RunState.RUNNING, created_at + timedelta(seconds=1))

    with pytest.raises(ValueError, match="canonical artifact"):
        running.transition(RunState.SUCCEEDED, created_at + timedelta(seconds=2))
    with pytest.raises(ValueError, match="provided together"):
        running.transition(
            RunState.SUCCEEDED,
            created_at + timedelta(seconds=2),
            artifact_uri="artifact://runs/result.json",
        )
    with pytest.raises(ValueError, match="scheme"):
        running.transition(
            RunState.SUCCEEDED,
            created_at + timedelta(seconds=2),
            artifact_uri="relative/result.json",
            artifact_digest=ARTIFACT_DIGEST,
        )
    with pytest.raises(ValueError, match="credentials"):
        running.transition(
            RunState.SUCCEEDED,
            created_at + timedelta(seconds=2),
            artifact_uri="https://user:secret@example.test/result.json",
            artifact_digest=ARTIFACT_DIGEST,
        )
    with pytest.raises(ValueError, match="query parameters"):
        running.transition(
            RunState.SUCCEEDED,
            created_at + timedelta(seconds=2),
            artifact_uri="https://example.test/result.json?token=secret",
            artifact_digest=ARTIFACT_DIGEST,
        )
