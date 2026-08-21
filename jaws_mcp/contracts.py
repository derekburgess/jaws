"""Versioned MCP v2 tool contracts retained independently of the transport SDK."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jaws.adapters.research_api import API_SCHEMA_VERSION, CAPABILITY_VERSION, ERROR_CODES

TOOL_SCHEMA_VERSION = "2.0.0"


def _object(properties: Mapping[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
    }


_DOCUMENT = {"type": "object", "description": "Versioned JAWS research contract document"}
_IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 256}

TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "research_capabilities": {
        "description": "Return MCP schema/capability versions, policy boundaries, limits, transports, and stable error codes.",
        "policy": "read",
        "input_schema": _object({}),
    },
    "research_orient": {
        "description": "List scoped datasets, captures, components, experiments, runs, benchmark summaries, and active jobs.",
        "policy": "read",
        "input_schema": _object({}),
    },
    "dataset_import": {
        "description": "Invoke the server-registered bounded importer for a catalog dataset ID; host paths are never accepted.",
        "policy": "mutation",
        "input_schema": _object({"dataset_id": _IDENTIFIER, "options": _DOCUMENT}, ("dataset_id",)),
    },
    "capture_enrich": {
        "description": "Invoke the server-registered enrichment service for a catalog capture ID with bounded options.",
        "policy": "mutation",
        "input_schema": _object({"capture_id": _IDENTIFIER, "options": _DOCUMENT}, ("capture_id",)),
    },
    "capture_profile": {
        "description": "Invoke the server-registered profiling service for a catalog capture ID and registered representation.",
        "policy": "mutation",
        "input_schema": _object(
            {
                "capture_id": _IDENTIFIER,
                "representation_id": _IDENTIFIER,
                "options": _DOCUMENT,
            },
            ("capture_id", "representation_id"),
        ),
    },
    "validate_hypothesis": {
        "description": "Validate a structured falsifiable hypothesis without executing work.",
        "policy": "mutation",
        "input_schema": _object({"document": _DOCUMENT}, ("document",)),
    },
    "validate_experiment": {
        "description": "Validate and canonicalize a versioned ExperimentSpec against the server catalog.",
        "policy": "mutation",
        "input_schema": _object({"document": _DOCUMENT}, ("document",)),
    },
    "explore_operation": {
        "description": "Run one small synchronous deterministic representation, reference, ranking, or evaluation operation.",
        "policy": "mutation",
        "input_schema": _object(
            {
                "document": _DOCUMENT,
                "variant": _IDENTIFIER,
                "stage": {
                    "type": "string",
                    "enum": ["represent", "reference", "rank", "evaluate"],
                },
            },
            ("document", "variant", "stage"),
        ),
    },
    "experiment_start": {
        "description": "Queue a bounded control/treatment experiment and immediately return job and experiment IDs.",
        "policy": "mutation",
        "input_schema": _object({"document": _DOCUMENT}, ("document",)),
    },
    "experiment_status": {
        "description": "Read the lifecycle state of an asynchronous experiment job.",
        "policy": "read",
        "input_schema": _object({"job_id": _IDENTIFIER}, ("job_id",)),
    },
    "experiment_result": {
        "description": "Retrieve a terminal experiment result with bounded finding pagination and structured evidence pointers.",
        "policy": "read",
        "input_schema": _object(
            {
                "job_id": _IDENTIFIER,
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            ("job_id",),
        ),
    },
    "experiment_cancel": {
        "description": "Request cooperative cancellation of an active experiment between deterministic stages.",
        "policy": "mutation",
        "input_schema": _object({"job_id": _IDENTIFIER}, ("job_id",)),
    },
    "inspect_evidence": {
        "description": "Validate and resolve the scope of a structured finding evidence pointer without arbitrary filesystem access.",
        "policy": "read",
        "input_schema": _object(
            {
                "pointer": _DOCUMENT,
                "peer_limit": {"type": "integer", "minimum": 0, "maximum": 100},
                "packet_limit": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            ("pointer",),
        ),
    },
}


def contract_snapshot() -> Mapping[str, Any]:
    return {
        "schema_version": TOOL_SCHEMA_VERSION,
        "api_schema_version": API_SCHEMA_VERSION,
        "capability_version": CAPABILITY_VERSION,
        "error_codes": ERROR_CODES,
        "tools": {name: TOOL_CONTRACTS[name] for name in sorted(TOOL_CONTRACTS)},
    }
