# MCP v2 research interface

MCP v2 exposes research application services, not CLI commands or database access. Install the
`mcp` extra, set `JAWS_ARTIFACT_ROOT` to a server-owned directory, and optionally set
`JAWS_MCP_CATALOG` to an operator-reviewed catalog. Client requests never choose credentials or
host paths.

## Workflow

1. Call `research_capabilities` and `research_orient`.
2. Use `validate_hypothesis`, then `validate_experiment`.
3. For a small declared fixture, call `explore_operation` with one of `represent`, `reference`,
   `rank`, or `evaluate`.
4. For a study, call `experiment_start`, poll `experiment_status`, then page through
   `experiment_result`. Call `experiment_cancel` to request cooperative cancellation.
5. Pass any returned finding's `evidence[0]` object directly to `inspect_evidence`.

The result envelope always carries `schema_version`, `capability_version`, and `ok`. Failures
carry `error.category`, stable `error.code`, a message, bounded details, and `retryable`.
`experiment_result` accepts zero-based `offset` and a `limit` no larger than the advertised
server maximum.

`dataset_import`, `capture_enrich`, and `capture_profile` operate only on IDs present in the
server catalog and only when the operator composition root registers the corresponding service.
They return `operation_unavailable` otherwise. There is no generic command, Cypher, capture, or
administration tool.

## Client configuration

Use `jaws_mcp/mcp-local.json` as a stdio template and `jaws_mcp/mcp-remote.json` for Streamable
HTTP. Bind HTTP to loopback unless an authenticated gateway supplies the remote security
boundary. The complete versioned JSON Schema snapshot is
`jaws_mcp/contracts/v2/tool-contracts.json`.

## Migration from MCP v1

The pipeline-oriented tools (`list_interfaces`, `capture_packets`, `document_organizations`,
`compute_embeddings`, `anomaly_detection`, `fetch_traffic`, and `inspect_endpoint`) are removed
from the research server. Use native/edge ingest for explicitly approved capture. Register
bounded import/enrichment/profile services by catalog ID, express ranking/evaluation in an
`ExperimentSpec`, and use the asynchronous lifecycle for the complete study. Legacy scripts
remain available as native CLI compatibility paths during the integrated rollout; MCP no longer
orchestrates them.
