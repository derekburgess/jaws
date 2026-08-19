"""Shared behavior for in-memory and Neo4j finding discovery indexes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CanonicalDigest,
    EntityId,
    ExperimentId,
    ExperimentRunIndex,
    FindingId,
    FindingIndex,
    FindingIndexBatch,
    OutlierStatus,
    RunId,
    RunState,
    ScoreDirection,
)
from jaws.ports import (
    DuplicateFindingError,
    FindingIndexConflictError,
    RunNotFoundError,
    RunStateConflictError,
)

SPECIFICATION_DIGEST = CanonicalDigest("1" * 64)
EXPERIMENT_ID = ExperimentId(str(SPECIFICATION_DIGEST))
STARTED_AT = datetime(2026, 8, 19, 14, tzinfo=UTC)


def _succeeded_run(repository, run_id: RunId, offset: int, digest: str) -> CanonicalDigest:
    artifact_digest = CanonicalDigest(digest * 64)
    created = ExperimentRunIndex(
        run_id=run_id,
        experiment_id=EXPERIMENT_ID,
        specification_digest=SPECIFICATION_DIGEST,
        state=RunState.CREATED,
        created_at=STARTED_AT + timedelta(seconds=offset),
    )
    repository.add(created)
    running = created.transition(RunState.RUNNING, created.created_at + timedelta(seconds=1))
    repository.transition(running, expected_state=RunState.CREATED)
    succeeded = running.transition(
        RunState.SUCCEEDED,
        created.created_at + timedelta(seconds=2),
        artifact_uri=f"artifact://runs/{run_id.value}.json",
        artifact_digest=artifact_digest,
    )
    repository.transition(succeeded, expected_state=RunState.RUNNING)
    return artifact_digest


def _finding(
    finding_id: str,
    run_id: RunId,
    entity_id: str,
    rank: int,
    score: float,
) -> FindingIndex:
    return FindingIndex(
        finding_id=FindingId(finding_id),
        run_id=run_id,
        entity_id=EntityId(entity_id),
        rank=rank,
        score=score,
        score_direction=ScoreDirection.HIGHER_IS_MORE_ANOMALOUS,
        outlier=OutlierStatus.OUTLIER if rank == 1 else OutlierStatus.INLIER,
    )


def assert_finding_repository_contract(repositories):
    """Exercise artifact binding, atomic idempotence, conflicts, and discovery."""

    missing_run = RunId("run_finding_missing")
    with pytest.raises(RunNotFoundError):
        repositories.findings.publish(FindingIndexBatch(missing_run, CanonicalDigest("9" * 64), ()))

    first_run = RunId("run_finding_first")
    created = ExperimentRunIndex(
        run_id=first_run,
        experiment_id=EXPERIMENT_ID,
        specification_digest=SPECIFICATION_DIGEST,
        state=RunState.CREATED,
        created_at=STARTED_AT,
    )
    repositories.experiments.add(created)
    with pytest.raises(RunStateConflictError):
        repositories.findings.publish(FindingIndexBatch(first_run, CanonicalDigest("a" * 64), ()))
    running = created.transition(RunState.RUNNING, STARTED_AT + timedelta(seconds=1))
    repositories.experiments.transition(running, expected_state=RunState.CREATED)
    first_artifact = CanonicalDigest("a" * 64)
    succeeded = running.transition(
        RunState.SUCCEEDED,
        STARTED_AT + timedelta(seconds=2),
        artifact_uri="artifact://runs/run_finding_first.json",
        artifact_digest=first_artifact,
    )
    repositories.experiments.transition(succeeded, expected_state=RunState.RUNNING)

    first = _finding("finding_first", first_run, "ip:192.0.2.10", 1, 9.5)
    second = _finding("finding_second", first_run, "ip:198.51.100.20", 2, 4.0)
    batch = FindingIndexBatch(first_run, first_artifact, (second, first))
    assert batch.findings == (first, second)
    with pytest.raises(FindingIndexConflictError, match="artifact checksum"):
        repositories.findings.publish(
            FindingIndexBatch(first_run, CanonicalDigest("b" * 64), (first, second))
        )

    assert repositories.findings.publish(batch) == 2
    assert repositories.findings.publish(batch) == 0
    assert repositories.findings.get(first.finding_id) == first
    assert repositories.findings.get(FindingId("finding_missing")) is None
    assert repositories.findings.list_for_run(first_run) == (first, second)
    with pytest.raises(FindingIndexConflictError, match="other content"):
        repositories.findings.publish(
            FindingIndexBatch(first_run, first_artifact, (replace(first, score=8.0), second))
        )

    second_run = RunId("run_finding_second")
    second_artifact = _succeeded_run(repositories.experiments, second_run, 10, "c")
    third = _finding("finding_third", second_run, first.entity_id.value, 1, 7.0)
    assert (
        repositories.findings.publish(FindingIndexBatch(second_run, second_artifact, (third,))) == 1
    )
    assert repositories.findings.list_for_entity(first.entity_id) == (first, third)

    duplicate_run = RunId("run_finding_duplicate")
    duplicate_artifact = _succeeded_run(repositories.experiments, duplicate_run, 20, "d")
    duplicate = replace(first, run_id=duplicate_run)
    with pytest.raises(DuplicateFindingError):
        repositories.findings.publish(
            FindingIndexBatch(duplicate_run, duplicate_artifact, (duplicate,))
        )
    assert repositories.findings.list_for_run(duplicate_run) == ()
    replacement = replace(duplicate, finding_id=FindingId("finding_after_rollback"))
    assert (
        repositories.findings.publish(
            FindingIndexBatch(duplicate_run, duplicate_artifact, (replacement,))
        )
        == 1
    )
    assert repositories.findings.list_for_run(duplicate_run) == (replacement,)

    empty_run = RunId("run_finding_empty")
    empty_artifact = _succeeded_run(repositories.experiments, empty_run, 30, "e")
    empty = FindingIndexBatch(empty_run, empty_artifact, ())
    assert repositories.findings.publish(empty) == 0
    assert repositories.findings.publish(empty) == 0
    with pytest.raises(FindingIndexConflictError, match="other content"):
        repositories.findings.publish(
            FindingIndexBatch(
                empty_run,
                empty_artifact,
                (_finding("finding_after_empty", empty_run, "ip:203.0.113.30", 1, 1.0),),
            )
        )
