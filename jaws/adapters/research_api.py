"""Bounded outer application facade shared by CLI, MCP, and the optional agent lab."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jaws.adapters.experiment_bundles import ExperimentBundleStore
from jaws.adapters.provenance import ProvenanceCollector
from jaws.adapters.run_journal import RunJournal
from jaws.domain import (
    ErrorCategory,
    EvidencePointer,
    HypothesisSpec,
    RunState,
    canonical_json,
    primitive,
    sensitive_key,
)
from jaws.research_codec import (
    DeclarativeResearchEngine,
    decode_experiment_spec,
    experiment_document,
)
from jaws.services.research import (
    ExperimentExecution,
    ExperimentService,
    HypothesizeService,
    ObserveService,
    OrientService,
    ResearchCatalog,
    ResearchRunRepository,
)

API_SCHEMA_VERSION = "2.0.0"
CAPABILITY_VERSION = "research-v2"
ERROR_CODES = (
    "cancel_conflict",
    "corrupt_artifact",
    "invalid_request",
    "job_not_found",
    "limit_exceeded",
    "operation_unavailable",
    "provider_unavailable",
)
_TOP_LEVEL_SPEC_FIELDS = frozenset(
    {
        "schema_version",
        "hypothesis",
        "observation",
        "entity",
        "representation",
        "reference",
        "ranker",
        "evaluator",
        "renderer",
        "dataset_ids",
        "evidence_digests",
        "label_source_versions",
        "deterministic_settings",
        "content_digest",
        "experiment_id",
    }
)
_FORBIDDEN_SPEC_FIELDS = frozenset(
    {"command", "cwd", "database_password", "docker_socket", "host_path", "shell"}
)


class ResearchAPIError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        category: ErrorCategory = ErrorCategory.VALIDATION,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.retryable = retryable
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ResearchLimits:
    max_concurrent_runs: int = 2
    max_request_bytes: int = 256_000
    max_page_size: int = 100
    max_fixture_entities: int = 10_000
    max_estimated_cost: float = 1.0

    def __post_init__(self) -> None:
        if (
            min(
                self.max_concurrent_runs,
                self.max_request_bytes,
                self.max_page_size,
                self.max_fixture_entities,
            )
            < 1
        ):
            raise ValueError("research limits must be positive")
        if self.max_estimated_cost < 0:
            raise ValueError("maximum estimated cost cannot be negative")


@dataclass(slots=True)
class _Job:
    job_id: str
    experiment_id: str
    specification: Any
    created_at: datetime
    cancellation: threading.Event = field(default_factory=threading.Event)
    state: str = "queued"
    future: Future[None] | None = None
    result: Mapping[str, Any] | None = None
    error: Mapping[str, Any] | None = None


class _EventCancellation:
    def __init__(self, event: threading.Event) -> None:
        self.event = event

    def is_cancelled(self) -> bool:
        return self.event.is_set()


def success(data: object) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "capability_version": CAPABILITY_VERSION,
        "ok": True,
        "data": primitive(data),
    }


def failure(error: ResearchAPIError) -> dict[str, Any]:
    return {
        "schema_version": API_SCHEMA_VERSION,
        "capability_version": CAPABILITY_VERSION,
        "ok": False,
        "error": {
            "category": error.category.value,
            "code": error.code,
            "message": str(error),
            "details": primitive(error.details),
            "retryable": error.retryable,
        },
    }


def exploratory_operation(
    specification: Any,
    variant: str,
    stage: Literal["represent", "reference", "rank", "evaluate"],
) -> Mapping[str, Any]:
    """Run one bounded deterministic stage; both CLI and MCP call this function."""

    engine = DeclarativeResearchEngine()
    representation = engine.represent(specification, variant)
    if stage == "represent":
        result = representation
    else:
        reference = engine.reference(specification, variant, representation)
        if stage == "reference":
            result = reference
        else:
            findings = engine.rank(specification, variant, representation, reference)
            if stage == "rank":
                result = findings
            else:
                from jaws.domain import RunId

                result = engine.evaluate(specification, variant, RunId("exploratory"), findings)
    return {"stage": stage, "result": primitive(result)}


class ResearchApplication:
    """Policy-enforcing facade with no capture, administration, or generic command surface."""

    def __init__(
        self,
        catalog: ResearchCatalog,
        artifact_root: Path,
        *,
        limits: ResearchLimits | None = None,
        repository_root: Path | None = None,
        operations: Mapping[str, Callable[[str, Mapping[str, Any]], object]] | None = None,
        inspection: Callable[[EvidencePointer, int, int], object] | None = None,
    ) -> None:
        self.catalog = catalog
        self.artifact_root = artifact_root.resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or ResearchLimits()
        self.repository_root = (repository_root or Path.cwd()).resolve()
        self.operations = dict(operations or {})
        self.inspection = inspection
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=self.limits.max_concurrent_runs,
            thread_name_prefix="jaws-research",
        )

    def capabilities(self) -> dict[str, Any]:
        return success(
            {
                "read_only_tools": (
                    "research_capabilities",
                    "research_orient",
                    "experiment_status",
                    "experiment_result",
                    "inspect_evidence",
                ),
                "mutation_tools": (
                    "dataset_import",
                    "capture_enrich",
                    "capture_profile",
                    "validate_hypothesis",
                    "validate_experiment",
                    "explore_operation",
                    "experiment_start",
                    "experiment_cancel",
                ),
                "administration_tools": (),
                "capture_enabled": False,
                "destructive_administration_enabled": False,
                "limits": primitive(self.limits),
                "registered_operations": tuple(sorted(self.operations)),
                "error_codes": ERROR_CODES,
                "transports": ("stdio", "streamable-http"),
            }
        )

    def orient(self) -> dict[str, Any]:
        snapshot = primitive(OrientService(self.catalog).orient())
        with self._lock:
            jobs = tuple(
                {
                    "job_id": item.job_id,
                    "experiment_id": item.experiment_id,
                    "state": item.state,
                }
                for item in sorted(self._jobs.values(), key=lambda value: value.created_at)
            )
        return success({"catalog": snapshot, "jobs": jobs})

    def validate_hypothesis(self, document: Mapping[str, Any]) -> dict[str, Any]:
        self._validate_document(document, allow_experiment=False)
        try:
            hypothesis = HypothesisSpec(
                claim=self._text(document, "claim"),
                control=self._text(document, "control"),
                treatments=self._texts(document, "treatments"),
                metrics=self._texts(document, "metrics"),
                regression_budgets=self._mapping(document.get("regression_budgets", {})),
                motivated_by_observation_id=self._optional_text(
                    document.get("motivated_by_observation_id")
                ),
            )
            HypothesizeService().validate(hypothesis)
        except (TypeError, ValueError) as error:
            raise ResearchAPIError("invalid_request", str(error)) from error
        return success({"hypothesis": hypothesis})

    def resource_operation(
        self,
        operation: Literal["dataset_import", "capture_enrich", "capture_profile"],
        resource_id: str,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        parameters = dict(options or {})
        self._validate_document(parameters, allow_experiment=False)
        if operation == "dataset_import":
            available = self.catalog.datasets
        else:
            available = self.catalog.captures
        if resource_id not in available:
            raise ResearchAPIError(
                "invalid_request",
                f"{operation} resource is outside the catalog scope: {resource_id}",
            )
        handler = self.operations.get(operation)
        if handler is None:
            raise ResearchAPIError(
                "operation_unavailable",
                f"{operation} is disabled by server policy",
                category=ErrorCategory.CONFIGURATION,
            )
        try:
            result = handler(resource_id, parameters)
        except (OSError, RuntimeError, ValueError) as error:
            raise ResearchAPIError(
                "operation_unavailable",
                str(error),
                category=ErrorCategory.UNAVAILABLE_DEPENDENCY,
                retryable=True,
            ) from error
        return success(
            {"operation": operation, "resource_id": resource_id, "result": primitive(result)}
        )

    def validate_experiment(self, document: Mapping[str, Any]) -> dict[str, Any]:
        specification = self._specification(document)
        return success({"specification": experiment_document(specification)})

    def explore(
        self,
        document: Mapping[str, Any],
        variant: str,
        stage: Literal["represent", "reference", "rank", "evaluate"],
    ) -> dict[str, Any]:
        specification = self._specification(document)
        try:
            data = exploratory_operation(specification, variant, stage)
        except (KeyError, TypeError, ValueError) as error:
            raise ResearchAPIError("invalid_request", str(error)) from error
        result = data["result"]
        if stage == "rank" and isinstance(result, list):
            result = result[: self.limits.max_page_size]
        return success({"experiment_id": specification.experiment_id, **data, "result": result})

    def start_experiment(self, document: Mapping[str, Any]) -> dict[str, Any]:
        specification = self._specification(document)
        with self._lock:
            active = sum(
                item.state in {"queued", "running", "cancelling"} for item in self._jobs.values()
            )
            if active >= self.limits.max_concurrent_runs:
                raise ResearchAPIError(
                    "limit_exceeded",
                    "concurrent experiment limit reached",
                    retryable=True,
                    details={"max_concurrent_runs": self.limits.max_concurrent_runs},
                )
            job_id = f"job-{uuid.uuid4()}"
            job = _Job(job_id, specification.experiment_id.value, specification, datetime.now(UTC))
            self._jobs[job_id] = job
            job.future = self._executor.submit(self._execute, job)
        return success(
            {
                "job_id": job_id,
                "experiment_id": specification.experiment_id,
                "state": "queued",
            }
        )

    def experiment_status(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        return success(
            {
                "job_id": job.job_id,
                "experiment_id": job.experiment_id,
                "state": job.state,
                "created_at": job.created_at,
                "error": job.error,
            }
        )

    def experiment_result(self, job_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if offset < 0 or limit < 1 or limit > self.limits.max_page_size:
            raise ResearchAPIError(
                "limit_exceeded",
                "result page is outside configured bounds",
                details={"max_page_size": self.limits.max_page_size},
            )
        job = self._job(job_id)
        if job.state not in {"completed", "failed", "cancelled"}:
            raise ResearchAPIError(
                "operation_unavailable", "experiment result is not terminal", retryable=True
            )
        if job.error is not None:
            raise ResearchAPIError(
                str(job.error.get("code", "operation_unavailable")),
                str(job.error.get("message", "experiment failed")),
                category=ErrorCategory.EXPERIMENT,
            )
        assert job.result is not None
        self._verify_result_bundles(job.result)
        findings = job.result.get("findings", ())
        if not isinstance(findings, list):
            raise ResearchAPIError(
                "corrupt_artifact",
                "stored findings are not an array",
                category=ErrorCategory.STORAGE,
            )
        return success(
            {
                **job.result,
                "findings": findings[offset : offset + limit],
                "page": {"offset": offset, "limit": limit, "total": len(findings)},
            }
        )

    def _verify_result_bundles(self, result: Mapping[str, Any]) -> None:
        runs = result.get("runs")
        if not isinstance(runs, list):
            raise ResearchAPIError(
                "corrupt_artifact", "stored runs are not an array", category=ErrorCategory.STORAGE
            )
        root = (self.artifact_root / "bundles").resolve()
        store = ExperimentBundleStore(root)
        for item in runs:
            if not isinstance(item, Mapping) or not isinstance(item.get("bundle"), str):
                raise ResearchAPIError(
                    "corrupt_artifact", "stored run has no bundle", category=ErrorCategory.STORAGE
                )
            path = Path(str(item["bundle"])).resolve()
            if not path.is_relative_to(root) or not store.verify(path).valid:
                raise ResearchAPIError(
                    "corrupt_artifact",
                    "experiment bundle failed scoped checksum verification",
                    category=ErrorCategory.STORAGE,
                )

    def cancel_experiment(self, job_id: str) -> dict[str, Any]:
        job = self._job(job_id)
        if job.state not in {"queued", "running"}:
            raise ResearchAPIError("cancel_conflict", "only an active experiment can be cancelled")
        job.cancellation.set()
        if job.future is not None and job.future.cancel():
            job.state = "cancelled"
        else:
            job.state = "cancelling"
        return success({"job_id": job.job_id, "state": job.state})

    def inspect_evidence(
        self,
        pointer: Mapping[str, Any],
        *,
        peer_limit: int = 20,
        packet_limit: int = 50,
    ) -> dict[str, Any]:
        if (
            min(peer_limit, packet_limit) < 0
            or max(peer_limit, packet_limit) > self.limits.max_page_size
        ):
            raise ResearchAPIError(
                "limit_exceeded",
                "inspection limits are outside configured bounds",
                details={"max_page_size": self.limits.max_page_size},
            )
        allowed = {"schema_version", "capture_id", "entity_id", "artifact_digest", "selector"}
        unknown = set(pointer) - allowed
        if unknown:
            raise ResearchAPIError("invalid_request", f"unknown evidence fields: {sorted(unknown)}")
        try:
            from jaws.domain import CanonicalDigest, CaptureId, EntityId, SchemaVersion

            evidence = EvidencePointer(
                schema_version=SchemaVersion(str(pointer.get("schema_version", "1.0.0"))),
                capture_id=(
                    CaptureId(str(value)) if (value := pointer.get("capture_id")) else None
                ),
                entity_id=(EntityId(str(value)) if (value := pointer.get("entity_id")) else None),
                artifact_digest=(
                    CanonicalDigest(str(value))
                    if (value := pointer.get("artifact_digest"))
                    else None
                ),
                selector=(str(value) if (value := pointer.get("selector")) else None),
            )
        except (TypeError, ValueError) as error:
            raise ResearchAPIError("invalid_request", str(error)) from error
        result = None
        if self.inspection is not None:
            try:
                result = self.inspection(evidence, peer_limit, packet_limit)
            except (OSError, RuntimeError, ValueError) as error:
                raise ResearchAPIError(
                    "operation_unavailable",
                    str(error),
                    category=ErrorCategory.UNAVAILABLE_DEPENDENCY,
                    retryable=True,
                ) from error
        return success(
            {
                "evidence": evidence,
                "scope": "capture" if evidence.capture_id is not None else "artifact",
                "resource_access": "scoped",
                "peer_limit": peer_limit,
                "packet_limit": packet_limit,
                "inspection": primitive(result),
            }
        )

    def _execute(self, job: _Job) -> None:
        job.state = "running"
        journal = RunJournal(self.artifact_root / "journal")
        bundles = ExperimentBundleStore(self.artifact_root / "bundles")
        service = ExperimentService(
            self.catalog,
            ResearchRunRepository(journal.write),
            bundles,
            ProvenanceCollector(self.repository_root),
        )
        try:
            executions = service.execute_matrix(
                job.specification,
                DeclarativeResearchEngine(),
                cancellation=_EventCancellation(job.cancellation),
            )
            observations: list[object] = []
            if len(executions) > 1 and executions[0].evaluation is not None:
                for treatment in executions[1:]:
                    if treatment.evaluation is not None:
                        observations.append(
                            ObserveService().observe(
                                job.specification,
                                executions[0].evaluation,
                                treatment.evaluation,
                            )
                        )
            findings = self._findings(executions)
            states = {item.run.state for item in executions}
            if RunState.FAILED in states:
                job.state = "failed"
            elif RunState.CANCELLED in states or job.cancellation.is_set():
                job.state = "cancelled"
            else:
                job.state = "completed"
            job.result = {
                "job_id": job.job_id,
                "experiment_id": job.experiment_id,
                "state": job.state,
                "runs": primitive(executions),
                "observations": primitive(observations),
                "findings": findings,
            }
        except Exception as error:  # service boundary converts unexpected failures once
            job.state = "failed"
            job.error = {"code": "operation_unavailable", "message": str(error)}

    @staticmethod
    def _findings(executions: tuple[ExperimentExecution, ...]) -> list[Mapping[str, Any]]:
        findings: list[Mapping[str, Any]] = []
        for execution in executions:
            if execution.run.state is not RunState.COMPLETED:
                continue
            path = Path(execution.bundle) / "artifacts" / execution.variant / "ranking.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, list):
                raise ValueError(f"ranking artifact is not an array: {path}")
            for item in value:
                if not isinstance(item, dict) or not item.get("evidence"):
                    raise ValueError("ranked finding has no structured evidence pointer")
                findings.append({"variant": execution.variant, **item})
        return findings

    def _specification(self, document: Mapping[str, Any]) -> Any:
        self._validate_document(document, allow_experiment=True)
        try:
            specification = decode_experiment_spec(document)
            self.catalog.validate(specification)
        except (KeyError, TypeError, ValueError) as error:
            raise ResearchAPIError("invalid_request", str(error)) from error
        return specification

    def _validate_document(self, document: Mapping[str, Any], *, allow_experiment: bool) -> None:
        size = len(canonical_json(document).encode())
        if size > self.limits.max_request_bytes:
            raise ResearchAPIError("limit_exceeded", "request exceeds configured byte limit")
        if allow_experiment:
            unknown = set(document) - _TOP_LEVEL_SPEC_FIELDS
            if unknown:
                raise ResearchAPIError(
                    "invalid_request", f"unknown experiment fields: {sorted(unknown)}"
                )
        self._reject_unsafe_fields(document)
        fixtures = document.get("deterministic_settings", {})
        if isinstance(fixtures, Mapping):
            fixture_map = fixtures.get("fixtures", {})
            if isinstance(fixture_map, Mapping):
                entity_count = sum(
                    len(value.get("ranking", ()))
                    for value in fixture_map.values()
                    if isinstance(value, Mapping) and isinstance(value.get("ranking", ()), list)
                )
                if entity_count > self.limits.max_fixture_entities:
                    raise ResearchAPIError("limit_exceeded", "fixture entity limit exceeded")
                estimated_cost = sum(
                    float(metrics.get("estimated_cost", 0.0))
                    for value in fixture_map.values()
                    if isinstance(value, Mapping)
                    and isinstance(metrics := value.get("metrics", {}), Mapping)
                )
                if estimated_cost > self.limits.max_estimated_cost:
                    raise ResearchAPIError(
                        "limit_exceeded",
                        "declared estimated cost exceeds server policy",
                        details={"max_estimated_cost": self.limits.max_estimated_cost},
                    )

    def _reject_unsafe_fields(self, value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).lower()
                if sensitive_key(key) or normalized in _FORBIDDEN_SPEC_FIELDS:
                    raise ResearchAPIError(
                        "invalid_request", f"secret or unrestricted field is prohibited: {key}"
                    )
                self._reject_unsafe_fields(item)
        elif isinstance(value, list):
            for item in value:
                self._reject_unsafe_fields(item)

    def _job(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ResearchAPIError("job_not_found", f"unknown experiment job: {job_id}")
        return job

    @staticmethod
    def _text(document: Mapping[str, Any], key: str) -> str:
        value = document.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a nonempty string")
        return value

    @staticmethod
    def _texts(document: Mapping[str, Any], key: str) -> tuple[str, ...]:
        value = document.get(key)
        if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
            raise ValueError(f"{key} must be a nonempty string array")
        return tuple(str(item) for item in value)

    @staticmethod
    def _mapping(value: object) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("value must be an object")
        return value

    @staticmethod
    def _optional_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("optional text cannot be empty")
        return value
