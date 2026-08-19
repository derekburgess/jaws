"""Neo4j experiment/run discovery index for canonical external artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from typing import Any, Protocol, Self, TypeVar, cast

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
    utc_text,
)
from jaws.ports import (
    DuplicateRunError,
    ExperimentDigestConflictError,
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


_RUN_FIELDS = """
run.RUN_ID AS run_id,
experiment.EXPERIMENT_ID AS experiment_id,
experiment.SPECIFICATION_SHA256 AS specification_sha256,
run.STATE AS state,
toString(run.CREATED_AT) AS created_at,
toString(run.STARTED_AT) AS started_at,
toString(run.ENDED_AT) AS ended_at,
run.ARTIFACT_URI AS artifact_uri,
run.ARTIFACT_SHA256 AS artifact_sha256,
run.FAILURE_CODE AS failure_code,
previous.RUN_ID AS supersedes_run_id
"""

_GET_QUERY = f"""
MATCH (run:JAWS_EXPERIMENT_RUN {{RUN_ID: $run_id}})-[:RUN_OF]->(experiment:JAWS_EXPERIMENT)
OPTIONAL MATCH (run)-[:SUPERSEDES]->(previous:JAWS_EXPERIMENT_RUN)
RETURN {_RUN_FIELDS}
"""

_LIST_FOR_EXPERIMENT_QUERY = f"""
MATCH (run:JAWS_EXPERIMENT_RUN)-[:RUN_OF]->(
    experiment:JAWS_EXPERIMENT {{EXPERIMENT_ID: $experiment_id}}
)
OPTIONAL MATCH (run)-[:SUPERSEDES]->(previous:JAWS_EXPERIMENT_RUN)
RETURN {_RUN_FIELDS}
ORDER BY run.CREATED_AT, run.RUN_ID
"""

_LIST_ALL_QUERY = f"""
MATCH (run:JAWS_EXPERIMENT_RUN)-[:RUN_OF]->(experiment:JAWS_EXPERIMENT)
OPTIONAL MATCH (run)-[:SUPERSEDES]->(previous:JAWS_EXPERIMENT_RUN)
RETURN {_RUN_FIELDS}
ORDER BY run.CREATED_AT, run.RUN_ID
"""

_SUPERSEDED_QUERY = """
MATCH (previous:JAWS_EXPERIMENT_RUN {RUN_ID: $run_id})-[:RUN_OF]->(experiment:JAWS_EXPERIMENT)
RETURN experiment.EXPERIMENT_ID AS experiment_id, previous.STATE AS state
"""

_ADD_QUERY = """
MERGE (experiment:JAWS_EXPERIMENT {EXPERIMENT_ID: $experiment_id})
ON CREATE SET experiment.SPECIFICATION_SHA256 = $specification_sha256
WITH experiment
WHERE experiment.SPECIFICATION_SHA256 = $specification_sha256
CREATE (run:JAWS_EXPERIMENT_RUN {
    RUN_ID: $run_id,
    STATE: $state,
    CREATED_AT: datetime($created_at)
})
CREATE (run)-[:RUN_OF]->(experiment)
WITH run
OPTIONAL MATCH (previous:JAWS_EXPERIMENT_RUN {RUN_ID: $supersedes_run_id})
FOREACH (_ IN CASE WHEN previous IS NULL THEN [] ELSE [1] END |
    CREATE (run)-[:SUPERSEDES]->(previous)
)
RETURN run.RUN_ID AS run_id
"""

_TRANSITION_QUERY = """
MATCH (run:JAWS_EXPERIMENT_RUN {RUN_ID: $run_id})
WHERE run.STATE = $expected_state
SET run.STATE = $state,
    run.STARTED_AT = CASE
        WHEN $started_at IS NULL THEN run.STARTED_AT
        ELSE datetime($started_at)
    END,
    run.ENDED_AT = CASE
        WHEN $ended_at IS NULL THEN run.ENDED_AT
        ELSE datetime($ended_at)
    END,
    run.ARTIFACT_URI = $artifact_uri,
    run.ARTIFACT_SHA256 = $artifact_sha256,
    run.FAILURE_CODE = $failure_code
RETURN run.RUN_ID AS run_id
"""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RepositorySchemaError(f"stored experiment run is missing {field_name}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime(value: object, field_name: str) -> datetime:
    return datetime.fromisoformat(_required_text(value, field_name).replace("Z", "+00:00"))


def _optional_datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _from_record(row: _Record) -> ExperimentRunIndex:
    artifact_digest = _optional_text(row["artifact_sha256"])
    supersedes_run_id = _optional_text(row["supersedes_run_id"])
    return ExperimentRunIndex(
        run_id=RunId(_required_text(row["run_id"], "RUN_ID")),
        experiment_id=ExperimentId(_required_text(row["experiment_id"], "EXPERIMENT_ID")),
        specification_digest=CanonicalDigest(
            _required_text(row["specification_sha256"], "SPECIFICATION_SHA256")
        ),
        state=RunState(_required_text(row["state"], "STATE")),
        created_at=_datetime(row["created_at"], "CREATED_AT"),
        started_at=_optional_datetime(row["started_at"]),
        ended_at=_optional_datetime(row["ended_at"]),
        artifact_uri=_optional_text(row["artifact_uri"]),
        artifact_digest=CanonicalDigest(artifact_digest) if artifact_digest else None,
        failure_code=_optional_text(row["failure_code"]),
        supersedes_run_id=RunId(supersedes_run_id) if supersedes_run_id else None,
    )


def _is_constraint_error(error: Exception) -> bool:
    code = str(getattr(error, "code", ""))
    return "Constraint" in type(error).__name__ or "Constraint" in code


class Neo4jExperimentIndexRepository:
    """Normalized experiment and append-only run index nodes in one database."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def add(self, record: ExperimentRunIndex) -> None:
        if record.state is not RunState.CREATED:
            raise ValueError("new experiment run index must start in created state")
        parameters: dict[str, object] = {
            "run_id": record.run_id.value,
            "experiment_id": record.experiment_id.value,
            "specification_sha256": str(record.specification_digest),
            "state": record.state.value,
            "created_at": utc_text(record.created_at),
            "supersedes_run_id": (
                record.supersedes_run_id.value if record.supersedes_run_id else None
            ),
        }

        def write(transaction: _Transaction) -> None:
            if transaction.run(_GET_QUERY, {"run_id": record.run_id.value}).single() is not None:
                raise DuplicateRunError(f"experiment run already exists: {record.run_id}")
            if record.supersedes_run_id is not None:
                previous = transaction.run(
                    _SUPERSEDED_QUERY, {"run_id": record.supersedes_run_id.value}
                ).single()
                if previous is None:
                    raise RunNotFoundError(
                        f"superseded experiment run does not exist: {record.supersedes_run_id}"
                    )
                if previous["experiment_id"] != record.experiment_id.value:
                    raise ValueError(
                        "an experiment run may supersede only a run of the same experiment"
                    )
                if RunState(_required_text(previous["state"], "STATE")) not in TERMINAL_RUN_STATES:
                    raise ValueError("an experiment run may supersede only a terminal run")
            created = transaction.run(_ADD_QUERY, parameters).single()
            if created is None:
                raise ExperimentDigestConflictError(
                    f"experiment digest does not match existing index: {record.experiment_id}"
                )

        try:
            with self._driver.session(database=self.database) as session:
                session.execute_write(write)
        except Exception as error:
            if isinstance(
                error,
                (DuplicateRunError, ExperimentDigestConflictError, RunNotFoundError, ValueError),
            ):
                raise
            if _is_constraint_error(error):
                raise DuplicateRunError(
                    f"experiment run already exists: {record.run_id}"
                ) from error
            raise

    def get(self, run_id: RunId) -> ExperimentRunIndex | None:
        with self._driver.session(database=self.database) as session:
            row = session.run(_GET_QUERY, {"run_id": run_id.value}).single()
        return _from_record(row) if row is not None else None

    def list_for_experiment(self, experiment_id: ExperimentId) -> tuple[ExperimentRunIndex, ...]:
        with self._driver.session(database=self.database) as session:
            rows = session.run(_LIST_FOR_EXPERIMENT_QUERY, {"experiment_id": experiment_id.value})
            return tuple(_from_record(row) for row in rows)

    def list_all(self) -> tuple[ExperimentRunIndex, ...]:
        with self._driver.session(database=self.database) as session:
            return tuple(_from_record(row) for row in session.run(_LIST_ALL_QUERY))

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
        parameters: dict[str, object] = {
            "run_id": record.run_id.value,
            "expected_state": expected_state.value,
            "state": record.state.value,
            "started_at": utc_text(record.started_at) if record.started_at else None,
            "ended_at": utc_text(record.ended_at) if record.ended_at else None,
            "artifact_uri": record.artifact_uri,
            "artifact_sha256": str(record.artifact_digest) if record.artifact_digest else None,
            "failure_code": record.failure_code,
        }
        with self._driver.session(database=self.database) as session:
            row = session.execute_write(
                lambda transaction: transaction.run(_TRANSITION_QUERY, parameters).single()
            )
        if row is None:
            current = self.get(record.run_id)
            if current is None:
                raise RunNotFoundError(f"experiment run does not exist: {record.run_id}")
            raise RunStateConflictError(
                f"experiment run {record.run_id} changed from expected state {expected_state}"
            )
