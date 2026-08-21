"""Typed, provider-neutral contracts for the optional OHEO laboratory."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from jaws.domain import REDACTED, HypothesisSpec, primitive, redact_text, sensitive_key

LAB_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    framework: str = "jaws-inhouse-oheo"
    framework_version: str = "1.0.0"
    provider: str = "scripted"
    model: str = "human-authored-fixture"
    instruction_version: str = "oheo-policy-v1"
    prompt_version: str = "oheo-prompts-v1"


@dataclass(frozen=True, slots=True)
class AgentBudget:
    max_tool_calls: int = 150
    max_model_units: int = 100_000
    max_estimated_cost: float = 0.0
    max_runtime_seconds: float = 30.0
    max_poll_attempts: int = 100

    def __post_init__(self) -> None:
        if (
            min(
                self.max_tool_calls,
                self.max_model_units,
                self.max_runtime_seconds,
                self.max_poll_attempts,
            )
            <= 0
        ):
            raise ValueError("agent budgets must be positive")
        if self.max_estimated_cost < 0:
            raise ValueError("agent cost budget cannot be negative")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    value: Mapping[str, Any]
    input_units: int = 0
    output_units: int = 0
    estimated_cost: float = 0.0

    def __post_init__(self) -> None:
        if min(self.input_units, self.output_units, self.estimated_cost) < 0:
            raise ValueError("model usage cannot be negative")


@dataclass(frozen=True, slots=True)
class ToolTrace:
    sequence: int
    operation: str
    at: datetime
    request: Mapping[str, Any]
    response: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ApprovalTrace:
    action: str
    target: str
    confirmation_digest: str


@dataclass(frozen=True, slots=True)
class AgentObservation:
    summary: str
    limitations: tuple[str, ...]
    experiment_id: str
    run_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    metric_deltas: Mapping[str, float]
    evidence: tuple[Mapping[str, Any], ...]
    follow_up: HypothesisSpec
    ground_truth_claimed: bool = False


@dataclass(frozen=True, slots=True)
class LabTrace:
    identity: AgentIdentity
    started_at: datetime
    completed_at: datetime
    tool_calls: tuple[ToolTrace, ...]
    approvals: tuple[ApprovalTrace, ...]
    usage: Mapping[str, float | int]
    produced_hypothesis: Mapping[str, Any]
    produced_experiment: Mapping[str, Any]
    observation: AgentObservation
    schema_version: str = LAB_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class OHEOResult:
    experiment_id: str
    job_id: str
    hypothesis: HypothesisSpec
    experiment: Mapping[str, Any]
    observation: AgentObservation
    trace: LabTrace
    schema_version: str = LAB_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class LabEvaluation:
    specification_valid: bool
    hypothesis_falsifiable: bool
    experiment_completed: bool
    evidence_citation_rate: float
    budget_adherent: bool
    repeated_run_consistent: bool | None
    matches_fixed_study: bool | None
    human_research_usefulness: float | None
    notes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = LAB_SCHEMA_VERSION


def redacted_lab_document(value: object) -> Any:
    """Serialize a laboratory artifact while withholding secrets and packet content."""

    return _redact(primitive(value))


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED
                if sensitive_key(key)
                or str(key).lower() in {"packet_payload", "payload", "raw_packet", "packet_bytes"}
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
