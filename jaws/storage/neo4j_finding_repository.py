"""Neo4j discovery index for canonical ranked-finding artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol, Self, TypeVar, cast

from jaws.domain import (
    EntityId,
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
    RepositorySchemaError,
    RunNotFoundError,
    RunStateConflictError,
)

ResultT = TypeVar("ResultT")


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def single(self) -> _Record | None: ...

    def consume(self) -> Any: ...


class _Transaction(Protocol):
    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Session(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...

    def execute_write(self, work: Callable[[_Transaction], ResultT]) -> ResultT: ...


class _Driver(Protocol):
    def session(self, *, database: str) -> _Session: ...


_FINDING_FIELDS = """
finding.FINDING_ID AS finding_id,
finding.RUN_ID AS run_id,
finding.ENTITY_ID AS entity_id,
finding.RANK AS rank,
finding.SCORE AS score,
finding.SCORE_DIRECTION AS score_direction,
finding.OUTLIER_STATUS AS outlier_status
"""

_GET_QUERY = f"""
MATCH (finding:JAWS_FINDING_INDEX {{FINDING_ID: $finding_id}})-[:IN_RUN]->(
    :JAWS_EXPERIMENT_RUN
)
RETURN {_FINDING_FIELDS}
"""

_LIST_RUN_QUERY = f"""
MATCH (finding:JAWS_FINDING_INDEX)-[:IN_RUN]->(
    :JAWS_EXPERIMENT_RUN {{RUN_ID: $run_id}}
)
RETURN {_FINDING_FIELDS}
ORDER BY finding.RANK
"""

_LIST_ENTITY_QUERY = f"""
MATCH (finding:JAWS_FINDING_INDEX {{ENTITY_ID: $entity_id}})-[:IN_RUN]->(
    :JAWS_EXPERIMENT_RUN
)
RETURN {_FINDING_FIELDS}
ORDER BY finding.RUN_ID, finding.RANK
"""

_RUN_PUBLICATION_QUERY = """
MATCH (run:JAWS_EXPERIMENT_RUN {RUN_ID: $run_id})
RETURN run.STATE AS state,
       run.ARTIFACT_SHA256 AS artifact_sha256,
       run.FINDING_INDEX_SHA256 AS finding_index_sha256,
       run.FINDING_COUNT AS finding_count
"""

_MARK_PUBLICATION_QUERY = """
MATCH (run:JAWS_EXPERIMENT_RUN {RUN_ID: $run_id})
WHERE run.FINDING_INDEX_SHA256 IS NULL
SET run.FINDING_INDEX_SHA256 = $finding_index_sha256,
    run.FINDING_COUNT = $finding_count
RETURN run.RUN_ID AS run_id
"""

_CREATE_FINDINGS_QUERY = """
UNWIND $findings AS item
MATCH (run:JAWS_EXPERIMENT_RUN {RUN_ID: $run_id})
CREATE (finding:JAWS_FINDING_INDEX {
    FINDING_ID: item.finding_id,
    RUN_ID: $run_id,
    ENTITY_ID: item.entity_id,
    RANK: item.rank,
    SCORE: item.score,
    SCORE_DIRECTION: item.score_direction,
    OUTLIER_STATUS: item.outlier_status
})
CREATE (finding)-[:IN_RUN]->(run)
"""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RepositorySchemaError(f"stored finding index is missing {field_name}")
    return value


def _from_record(row: _Record) -> FindingIndex:
    rank = row["rank"]
    score = row["score"]
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise RepositorySchemaError("stored finding rank must be an integer")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        raise RepositorySchemaError("stored finding score must be numeric")
    return FindingIndex(
        finding_id=FindingId(_required_text(row["finding_id"], "FINDING_ID")),
        run_id=RunId(_required_text(row["run_id"], "RUN_ID")),
        entity_id=EntityId(_required_text(row["entity_id"], "ENTITY_ID")),
        rank=rank,
        score=float(score),
        score_direction=ScoreDirection(_required_text(row["score_direction"], "SCORE_DIRECTION")),
        outlier=OutlierStatus(_required_text(row["outlier_status"], "OUTLIER_STATUS")),
    )


def _is_constraint_error(error: Exception) -> bool:
    code = str(getattr(error, "code", ""))
    return "Constraint" in type(error).__name__ or "Constraint" in code


def _matches_publication(
    row: _Record,
    batch: FindingIndexBatch,
    stored: tuple[FindingIndex, ...],
) -> bool:
    digest = row["finding_index_sha256"]
    count = row["finding_count"]
    return (
        isinstance(digest, str)
        and digest == str(batch.digest)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count == len(batch.findings)
        and stored == batch.findings
    )


class Neo4jFindingRepository:
    """Atomic, idempotent finding index publication for finalized runs."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def publish(self, batch: FindingIndexBatch) -> int:
        parameters: dict[str, object] = {
            "run_id": batch.run_id.value,
            "finding_index_sha256": str(batch.digest),
            "finding_count": len(batch.findings),
            "findings": [
                {
                    "finding_id": finding.finding_id.value,
                    "entity_id": finding.entity_id.value,
                    "rank": finding.rank,
                    "score": finding.score,
                    "score_direction": finding.score_direction.value,
                    "outlier_status": finding.outlier.value,
                }
                for finding in batch.findings
            ],
        }

        def write(transaction: _Transaction) -> int:
            row = transaction.run(_RUN_PUBLICATION_QUERY, {"run_id": batch.run_id.value}).single()
            if row is None:
                raise RunNotFoundError(f"experiment run does not exist: {batch.run_id}")
            state = RunState(_required_text(row["state"], "STATE"))
            if state is not RunState.SUCCEEDED:
                raise RunStateConflictError(
                    f"finding index requires succeeded run; {batch.run_id} is {state}"
                )
            if row["artifact_sha256"] != str(batch.artifact_digest):
                raise FindingIndexConflictError(
                    "finding index artifact checksum does not match the finalized run"
                )
            if row["finding_index_sha256"] is not None:
                stored = tuple(
                    _from_record(item)
                    for item in transaction.run(_LIST_RUN_QUERY, {"run_id": batch.run_id.value})
                )
                if _matches_publication(row, batch, stored):
                    return 0
                raise FindingIndexConflictError(
                    "finding index was already published with other content"
                )
            marked = transaction.run(_MARK_PUBLICATION_QUERY, parameters).single()
            if marked is None:
                current = transaction.run(
                    _RUN_PUBLICATION_QUERY, {"run_id": batch.run_id.value}
                ).single()
                stored = tuple(
                    _from_record(item)
                    for item in transaction.run(_LIST_RUN_QUERY, {"run_id": batch.run_id.value})
                )
                if current is not None and _matches_publication(current, batch, stored):
                    return 0
                raise FindingIndexConflictError("finding index changed during publication")
            if batch.findings:
                transaction.run(_CREATE_FINDINGS_QUERY, parameters).consume()
            return len(batch.findings)

        try:
            with self._driver.session(database=self.database) as session:
                return session.execute_write(write)
        except Exception as error:
            if isinstance(
                error,
                (FindingIndexConflictError, RunNotFoundError, RunStateConflictError),
            ):
                raise
            if _is_constraint_error(error):
                raise DuplicateFindingError(
                    "finding index identity already exists in another publication"
                ) from error
            raise

    def get(self, finding_id: FindingId) -> FindingIndex | None:
        with self._driver.session(database=self.database) as session:
            row = session.run(_GET_QUERY, {"finding_id": finding_id.value}).single()
        return _from_record(row) if row is not None else None

    def list_for_run(self, run_id: RunId) -> tuple[FindingIndex, ...]:
        with self._driver.session(database=self.database) as session:
            rows = session.run(_LIST_RUN_QUERY, {"run_id": run_id.value})
            return tuple(_from_record(row) for row in rows)

    def list_for_entity(self, entity_id: EntityId) -> tuple[FindingIndex, ...]:
        with self._driver.session(database=self.database) as session:
            rows = session.run(_LIST_ENTITY_QUERY, {"entity_id": entity_id.value})
            return tuple(_from_record(row) for row in rows)
