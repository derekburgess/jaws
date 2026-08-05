# ADR-0009: Use immutable standard-library domain contracts

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-05 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0004](0004-separate-experiment-specifications-and-runs.md), [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#milestone-1--project-foundation-and-typed-contracts) |

## Context

The research workbench needs stable identities, specifications, evidence joins, rankings,
lifecycle rules, timestamps, and units before storage and services can be separated from
the legacy command modules. These contracts must import in the numerical core without a
database, model provider, CLI, MCP, or validation framework.

## Decision

`jaws.domain` uses frozen, slotted dataclasses, enums, runtime-distinct identifier value
objects, `NewType` scalar aliases, and protocols from the Python standard library.
Experiment-facing specifications are immutable after construction. A canonical JSON
encoding normalizes UTC timestamps, sorts object keys, rejects non-finite numbers, and
produces SHA-256 specification digests. Measurements always carry units. Lifecycle
transitions are explicit. Rankings order by declared score direction and then stable
entity identity, so equal scores do not depend on input order. Continuous scores and
categorical outlier status remain separate fields.

## Alternatives considered

Pydantic or another schema framework could provide richer coercion and generated schemas,
but would add a dependency to the innermost package and make behavior depend on that
framework's version. Plain dictionaries would avoid dependencies but would not enforce
immutability, identity distinctions, lifecycle rules, or type-checkable joins.

## Consequences

The core contracts remain lightweight and deterministic, and adapters can validate or
render them without owning their semantics. Explicit constructors require more code than
permissive dictionaries. JSON Schema generation and external input coercion remain adapter
responsibilities until a later milestone selects those mechanisms.

## Benchmark impact

This is an internal representation change only. Detector features, scores, ordering, and
Benchmark 0 artifacts remain unchanged; frozen compatibility and smoke checks guard that
boundary.

## Migration

Milestone 1 introduces the package and its contracts. Later storage, service, and adapter
milestones replace legacy dictionaries incrementally while retaining compatibility
serializers at public boundaries.

## Reversal conditions

Reconsider if standard-library validation cannot express a required contract or measured
maintenance cost exceeds the dependency boundary benefit. A replacement must preserve
canonical identity, immutability, lightweight imports, and frozen external behavior.
