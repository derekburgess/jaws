# ADR-0019: MCP v2 lifecycle and Streamable HTTP

## Status

Accepted

## Context

The original MCP adapter invoked CLI subprocesses, opened the database adapter itself, and
duplicated detector explanations. Long requests had process/client timeout ambiguity, and
client-provided arguments were too close to runtime paths and provider configuration.

## Decision

MCP v2 is a transport-only adapter over `ResearchApplication`. Small deterministic stages
remain synchronous. Experiments use start/status/result/cancel with an immediate job ID and
canonical experiment ID, cooperative cancellation between stages, bounded concurrency, and
bounded result pagination. The two supported transports are stdio and Streamable HTTP.

Tool schemas, error codes, capability version, and policy classification are retained under
`jaws_mcp/contracts/v2/`. Read tools are distinct from mutation tools. Capture and destructive
administration do not exist in the research server. Import, enrichment, and profiling accept
catalog resource IDs and call only operator-registered service handlers; arbitrary paths,
commands, secret fields, and generic experiment extensions are rejected.

## Consequences

CLI and MCP share the deterministic exploratory function, and all transport errors use one
versioned envelope. A server without an operator-registered provider returns a typed
`operation_unavailable` error. Job state is process-local while canonical run journals and
bundles are durable; a later protocol version may add a distributed queue without changing
the tool lifecycle.
