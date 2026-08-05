# ADR-0010: Separate versioned service results from legacy envelopes

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-05 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [CLI compatibility contract](../../benchmarks/baseline-0/compatibility/cli-contract.json), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#compatibility-ledger) |

## Context

Services need typed, versioned success and failure results with stable error categories and
codes. Existing CLI and MCP clients already consume a flat `{ "ok": ... }` JSON object,
whose exact bytes are frozen by Benchmark 0. Replacing that shape immediately would turn an
internal refactor into an undocumented interface break.

## Decision

Domain services return `Success[T]` or `Failure`, serialized as a versioned nested envelope.
Failures contain a stable category, code, message, details, and retryability signal. Legacy
CLI and MCP adapters use one compatibility serializer that retains the existing flat shape,
key order, indentation, and message-only error behavior. New service metadata is not leaked
into the legacy surface.

## Alternatives considered

Changing every public response to the modern envelope immediately would eliminate the
adapter but break frozen clients. Keeping untyped dictionaries everywhere would preserve
bytes but perpetuate ambiguous failures and prevent shared service contracts.

## Consequences

Services gain one typed contract while existing clients remain stable. Two serializers
coexist during rollout, and the legacy surface cannot expose every structured error field.
Any future public migration requires an explicit versioned interface decision.

## Benchmark impact

No external behavior changes. Regenerating all seven CLI cases must match the frozen stream
bytes, parsed envelopes, stderr, and exit status exactly. Benchmark rankings are unaffected.

## Migration

Reporter and MCP subprocess fallback paths delegate to the compatibility helpers first.
Later CLI and MCP versions may expose the structured envelope under an explicit version
without changing the legacy commands.

## Reversal conditions

Remove the compatibility serializer only after a documented public migration and parity
period. Any replacement must retain stable error codes internally and an explicit schema
version externally.
