"""Small in-house Orient → Hypothesize → Experiment → Observe orchestrator."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from jaws.domain import HypothesisSpec, canonical_json, primitive
from jaws.research_codec import decode_experiment_spec

from .contracts import (
    AgentBudget,
    AgentIdentity,
    AgentObservation,
    ApprovalTrace,
    LabTrace,
    ModelResponse,
    OHEOResult,
    ToolTrace,
)

_INJECTION = re.compile(
    r"(?i)(ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"api[_ -]?key|password|secret|docker\.sock|cypher|shell\s+command|packet\s+payload)"
)
_GROUND_TRUTH = re.compile(r"(?i)\b(confirmed|proven|definitely)\s+(malicious|threat|attack)\b")


class ResearchGateway(Protocol):
    def orient(self) -> dict[str, Any]: ...

    def validate_hypothesis(self, document: Mapping[str, Any]) -> dict[str, Any]: ...

    def validate_experiment(self, document: Mapping[str, Any]) -> dict[str, Any]: ...

    def start_experiment(self, document: Mapping[str, Any]) -> dict[str, Any]: ...

    def experiment_status(self, job_id: str) -> dict[str, Any]: ...

    def experiment_result(
        self, job_id: str, *, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]: ...

    def cancel_experiment(self, job_id: str) -> dict[str, Any]: ...


class ResearchModel(Protocol):
    identity: AgentIdentity

    def hypothesize(self, orientation: Mapping[str, Any]) -> ModelResponse: ...

    def design(
        self, orientation: Mapping[str, Any], hypothesis: Mapping[str, Any]
    ) -> ModelResponse: ...

    def observe(self, result: Mapping[str, Any]) -> ModelResponse: ...


class ApprovalRequired(RuntimeError):
    def __init__(self, expected_confirmation: str) -> None:
        super().__init__(f"explicit approval required: {expected_confirmation}")
        self.expected_confirmation = expected_confirmation


@dataclass(frozen=True, slots=True)
class LaboratoryPolicy:
    held_out_identifiers: frozenset[str] = frozenset()
    allow_live_capture: bool = False
    allow_new_dataset_acquisition: bool = False
    allow_external_cost: bool = False
    allow_mutation: bool = True

    @property
    def prohibited_capabilities(self) -> tuple[str, ...]:
        return (
            "shell",
            "generated_python",
            "neo4j_credentials",
            "cypher",
            "database_deletion",
            "host_filesystem",
            "docker_socket",
            "packet_capture",
            "held_out_labels",
            "ranker_self_modification",
        )


class _Ledger:
    def __init__(self, budget: AgentBudget, started: float) -> None:
        self.budget = budget
        self.started = started
        self.tool_calls = 0
        self.input_units = 0
        self.output_units = 0
        self.estimated_cost = 0.0

    def tool(self) -> None:
        self.tool_calls += 1
        self.check()

    def model(self, response: ModelResponse) -> None:
        self.input_units += response.input_units
        self.output_units += response.output_units
        self.estimated_cost += response.estimated_cost
        self.check()

    def check(self) -> None:
        if self.tool_calls > self.budget.max_tool_calls:
            raise RuntimeError("agent tool-call budget exceeded")
        if self.input_units + self.output_units > self.budget.max_model_units:
            raise RuntimeError("agent model-unit budget exceeded")
        if self.estimated_cost > self.budget.max_estimated_cost:
            raise RuntimeError("agent external-cost budget exceeded")
        if time.monotonic() - self.started > self.budget.max_runtime_seconds:
            raise RuntimeError("agent runtime budget exceeded")


class InHouseOHEO:
    """Deterministic state machine around an untrusted proposal model."""

    def __init__(
        self,
        gateway: ResearchGateway,
        *,
        policy: LaboratoryPolicy | None = None,
        budget: AgentBudget | None = None,
    ) -> None:
        self.gateway = gateway
        self.policy = policy or LaboratoryPolicy()
        self.budget = budget or AgentBudget()

    def run(self, model: ResearchModel, *, confirmations: Sequence[str] = ()) -> OHEOResult:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        ledger = _Ledger(self.budget, started)
        calls: list[ToolTrace] = []
        approvals: list[ApprovalTrace] = []

        orientation = self._tool("research_orient", {}, self.gateway.orient, ledger, calls)
        safe_orientation = self._sanitize_orientation(self._data(orientation))
        hypothesis_response = model.hypothesize(safe_orientation)
        ledger.model(hypothesis_response)
        hypothesis_document = dict(hypothesis_response.value)
        validated_hypothesis = self._tool(
            "validate_hypothesis",
            hypothesis_document,
            lambda: self.gateway.validate_hypothesis(hypothesis_document),
            ledger,
            calls,
        )
        hypothesis_value = self._data(validated_hypothesis)["hypothesis"]

        experiment_response = model.design(safe_orientation, hypothesis_document)
        ledger.model(experiment_response)
        experiment_document = dict(experiment_response.value)
        self._enforce_experiment_policy(experiment_document)
        validated_experiment = self._tool(
            "validate_experiment",
            experiment_document,
            lambda: self.gateway.validate_experiment(experiment_document),
            ledger,
            calls,
        )
        experiment = self._data(validated_experiment)["specification"]
        experiment_id = str(experiment["experiment_id"])
        self._approve("experiment_start", experiment_id, confirmations, approvals)
        if self._declared_cost(experiment_document) > 0:
            if not self.policy.allow_external_cost:
                raise RuntimeError("external-cost experiments are disabled by laboratory policy")
            self._approve("external_cost", experiment_id, confirmations, approvals)

        started_job = self._tool(
            "experiment_start",
            {"experiment_id": experiment_id},
            lambda: self.gateway.start_experiment(experiment_document),
            ledger,
            calls,
        )
        job_id = str(self._data(started_job)["job_id"])
        terminal: Mapping[str, Any] | None = None
        try:
            for _ in range(self.budget.max_poll_attempts):
                status = self._tool(
                    "experiment_status",
                    {"job_id": job_id},
                    lambda: self.gateway.experiment_status(job_id),
                    ledger,
                    calls,
                )
                terminal = self._data(status)
                if terminal["state"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)
                ledger.check()
            else:
                raise RuntimeError("agent poll-attempt budget exceeded")
        except RuntimeError:
            self._tool(
                "experiment_cancel",
                {"job_id": job_id},
                lambda: self.gateway.cancel_experiment(job_id),
                ledger,
                calls,
                count=False,
            )
            raise
        if terminal is None or terminal["state"] != "completed":
            raise RuntimeError(f"experiment did not complete: {terminal}")

        result_envelope = self._tool(
            "experiment_result",
            {"job_id": job_id, "offset": 0, "limit": 100},
            lambda: self.gateway.experiment_result(job_id, offset=0, limit=100),
            ledger,
            calls,
        )
        result = self._data(result_envelope)
        observation_response = model.observe(self._sanitize_orientation(result))
        ledger.model(observation_response)
        observation = self._observation(result, observation_response.value, hypothesis_value)
        completed_at = datetime.now(UTC)
        trace = LabTrace(
            identity=model.identity,
            started_at=started_at,
            completed_at=completed_at,
            tool_calls=tuple(calls),
            approvals=tuple(approvals),
            usage={
                "tool_calls": ledger.tool_calls,
                "model_input_units": ledger.input_units,
                "model_output_units": ledger.output_units,
                "estimated_cost": ledger.estimated_cost,
                "runtime_seconds": time.monotonic() - started,
            },
            produced_hypothesis=primitive(hypothesis_value),
            produced_experiment=experiment,
            observation=observation,
        )
        return OHEOResult(
            experiment_id,
            job_id,
            self._hypothesis(hypothesis_value),
            experiment,
            observation,
            trace,
        )

    def _tool(
        self,
        operation: str,
        request: Mapping[str, Any],
        function: Any,
        ledger: _Ledger,
        calls: list[ToolTrace],
        *,
        count: bool = True,
    ) -> Mapping[str, Any]:
        if count:
            ledger.tool()
        response = function()
        if not isinstance(response, Mapping) or response.get("ok") is not True:
            raise RuntimeError(f"registered research operation failed: {operation}")
        calls.append(
            ToolTrace(
                len(calls) + 1,
                operation,
                datetime.now(UTC),
                self._trace_summary(request),
                self._trace_summary(response),
            )
        )
        return response

    def _approve(
        self,
        action: str,
        target: str,
        confirmations: Sequence[str],
        approvals: list[ApprovalTrace],
    ) -> None:
        if action == "experiment_start" and not self.policy.allow_mutation:
            raise RuntimeError("experiment mutation is disabled by laboratory policy")
        expected = f"approve:{action}:{target}"
        if expected not in confirmations:
            raise ApprovalRequired(expected)
        approvals.append(
            ApprovalTrace(action, target, hashlib.sha256(expected.encode()).hexdigest())
        )

    def _enforce_experiment_policy(self, document: Mapping[str, Any]) -> None:
        specification = decode_experiment_spec(document)
        requested = {item.value for item in specification.dataset_ids}
        forbidden = requested & self.policy.held_out_identifiers
        if forbidden:
            raise RuntimeError(
                f"held-out datasets are unavailable to the agent: {sorted(forbidden)}"
            )
        if any(
            key in canonical_json(document).lower()
            for key in ("command", "host_path", "docker_socket")
        ):
            raise RuntimeError("experiment requests a prohibited capability")
        self._reject_sensitive_evidence(document)

    def _observation(
        self,
        result: Mapping[str, Any],
        proposed: Mapping[str, Any],
        hypothesis: Mapping[str, Any],
    ) -> AgentObservation:
        summary = str(proposed.get("summary", "Experiment completed with deterministic metrics."))
        if _GROUND_TRUTH.search(summary):
            raise RuntimeError("agent observation attempts to assert threat ground truth")
        limitations_value = proposed.get("limitations", ())
        if not isinstance(limitations_value, Sequence) or isinstance(limitations_value, str):
            raise RuntimeError("observation limitations must be an array")
        runs = result.get("runs", ())
        observations = result.get("observations", ())
        findings = result.get("findings", ())
        run_ids = tuple(
            str(run["run"]["run_id"])
            for run in runs
            if isinstance(run, Mapping) and isinstance(run.get("run"), Mapping)
        )
        observation_ids = tuple(
            str(item["observation_id"])
            for item in observations
            if isinstance(item, Mapping) and item.get("observation_id")
        )
        metric_deltas: dict[str, float] = {}
        for item in observations:
            if isinstance(item, Mapping) and isinstance(item.get("metric_deltas"), Mapping):
                metric_deltas.update(
                    {str(key): float(value) for key, value in item["metric_deltas"].items()}
                )
        evidence = tuple(
            pointer
            for finding in findings
            if isinstance(finding, Mapping)
            for pointer in finding.get("evidence", ())
            if isinstance(pointer, Mapping)
        )
        motivation = observation_ids[0] if observation_ids else run_ids[0]
        follow_up = HypothesisSpec(
            claim=str(
                proposed.get(
                    "follow_up_claim",
                    "Repeating the treatment on another declared validation window preserves the measured direction",
                )
            ),
            control=str(hypothesis["control"]),
            treatments=tuple(str(item) for item in hypothesis["treatments"]),
            metrics=tuple(str(item) for item in hypothesis["metrics"]),
            metric_objectives=dict(hypothesis.get("metric_objectives", {})),
            regression_budgets=dict(hypothesis.get("regression_budgets", {})),
            motivated_by_observation_id=motivation,
        )
        if not run_ids or not evidence:
            raise RuntimeError("agent observation lacks retained run/evidence citations")
        return AgentObservation(
            summary,
            tuple(str(item) for item in limitations_value),
            str(result["experiment_id"]),
            run_ids,
            observation_ids,
            metric_deltas,
            evidence,
            follow_up,
        )

    def _sanitize_orientation(self, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(key): self._sanitize_orientation(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [self._sanitize_orientation(item) for item in value]
        if isinstance(value, str) and _INJECTION.search(value):
            return "[untrusted metadata withheld]"
        return value

    def _reject_sensitive_evidence(self, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).lower() in {
                    "packet_payload",
                    "payload",
                    "raw_packet",
                    "packet_bytes",
                }:
                    raise RuntimeError("packet content is prohibited in agent specifications")
                self._reject_sensitive_evidence(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                self._reject_sensitive_evidence(item)

    @staticmethod
    def _data(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        data = envelope.get("data")
        if not isinstance(data, Mapping):
            raise RuntimeError("research operation returned no typed data")
        return data

    @staticmethod
    def _trace_summary(value: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = {
            key: item
            for key, item in value.items()
            if key
            in {
                "job_id",
                "experiment_id",
                "offset",
                "limit",
                "ok",
                "schema_version",
                "capability_version",
            }
        }
        return cast(Mapping[str, Any], primitive(allowed))

    @staticmethod
    def _hypothesis(value: Mapping[str, Any]) -> HypothesisSpec:
        return HypothesisSpec(
            claim=str(value["claim"]),
            control=str(value["control"]),
            treatments=tuple(str(item) for item in value["treatments"]),
            metrics=tuple(str(item) for item in value["metrics"]),
            metric_objectives=dict(value.get("metric_objectives", {})),
            regression_budgets=dict(value.get("regression_budgets", {})),
            motivated_by_observation_id=(
                str(item) if (item := value.get("motivated_by_observation_id")) else None
            ),
        )

    @staticmethod
    def _declared_cost(document: Mapping[str, Any]) -> float:
        settings = document.get("deterministic_settings", {})
        if not isinstance(settings, Mapping):
            return 0.0
        fixtures = settings.get("fixtures", {})
        if not isinstance(fixtures, Mapping):
            return 0.0
        return sum(
            float(metrics.get("estimated_cost", 0.0))
            for fixture in fixtures.values()
            if isinstance(fixture, Mapping)
            and isinstance(metrics := fixture.get("metrics", {}), Mapping)
        )
