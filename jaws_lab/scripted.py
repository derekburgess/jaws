"""Deterministic human-authored model adapter for the removable laboratory spike."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import AgentIdentity, ModelResponse


class ScriptedResearchModel:
    """Return a reviewed study document without importing or calling an LLM provider."""

    identity = AgentIdentity()

    def __init__(self, experiment: Mapping[str, Any]) -> None:
        self.experiment = dict(experiment)

    def hypothesize(self, orientation: Mapping[str, Any]) -> ModelResponse:
        value = self.experiment.get("hypothesis")
        if not isinstance(value, Mapping):
            raise ValueError("scripted experiment has no hypothesis object")
        return ModelResponse(
            dict(value), input_units=len(str(orientation)), output_units=len(str(value))
        )

    def design(
        self, orientation: Mapping[str, Any], hypothesis: Mapping[str, Any]
    ) -> ModelResponse:
        return ModelResponse(
            self.experiment,
            input_units=len(str(orientation)) + len(str(hypothesis)),
            output_units=len(str(self.experiment)),
        )

    def observe(self, result: Mapping[str, Any]) -> ModelResponse:
        experiment_id = str(result.get("experiment_id", "unknown"))
        return ModelResponse(
            {
                "summary": (
                    f"Experiment {experiment_id} produced deterministic comparative metrics; "
                    "the result prioritizes follow-up inspection and does not establish threat ground truth."
                ),
                "limitations": [
                    "Anomaly rank is not a threat label.",
                    "The declared dataset and observation window bound this result.",
                ],
            },
            input_units=len(str(result)),
            output_units=64,
        )
