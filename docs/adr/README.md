# JAWS Architecture Decision Records

Architecture decision records (ADRs) preserve the reasoning behind decisions that
shape JAWS across milestones. They complement, rather than replace, the project
definition in [`README.md`](../../README.md) and the execution contract in
[`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md).

## Initial decision set

The following records establish the fixed research and architecture contract for
the research-workbench rollout.

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-network-anomaly-ranking-research-workbench.md) | Accepted | Define JAWS as a network anomaly-ranking research workbench |
| [0002](0002-analytical-axes-and-research-operations.md) | Accepted | Separate four analytical axes and seven research operations |
| [0003](0003-shared-research-terminology.md) | Accepted | Adopt shared research terminology |
| [0004](0004-separate-experiment-specifications-and-runs.md) | Accepted | Separate immutable experiment specifications from execution runs |
| [0005](0005-evidence-store-and-portable-experiment-records.md) | Accepted | Use Neo4j for evidence and portable bundles for experiment records |
| [0006](0006-deterministic-core-and-adapter-boundaries.md) | Accepted | Keep the deterministic core independent of interfaces, storage, and agents |
| [0007](0007-freeze-benchmark-zero-before-detector-refactoring.md) | Accepted | Freeze Benchmark 0 before detector refactoring |
| [0008](0008-capability-extras-and-direct-constraints.md) | Accepted | Use capability extras and reviewed direct constraints |
| [0009](0009-standard-library-domain-contracts.md) | Accepted | Use immutable standard-library domain contracts |
| [0010](0010-versioned-service-and-legacy-result-envelopes.md) | Accepted | Separate versioned service results from legacy envelopes |

Milestone-specific choices that are not yet due remain in the implementation
plan's [decision queue](../../IMPLEMENTATION_PLAN.md#decision-queue). In
particular, these ADRs do not prematurely choose a schema library, ID format,
artifact encoding, container image, MCP job protocol, or agent framework.

## Status values

- **Proposed** — under consideration and not yet binding.
- **Accepted** — the active decision and a project constraint.
- **Deprecated** — retained for history but no longer recommended.
- **Superseded** — replaced by a later ADR, which the record must identify.
- **Rejected** — considered and deliberately not adopted.

## Process

1. Copy [`template.md`](template.md) and assign the next four-digit number.
2. State one consequential decision. Split unrelated choices into separate ADRs.
3. Identify alternatives and costs, not only the preferred outcome.
4. Describe benchmark and migration effects before accepting the record.
5. Link the ADR from this index and from the relevant milestone or code change.
6. Use a new ADR to reverse or materially change an accepted decision.

Accepted ADRs are historical records. Typographical corrections and clarifying
links may be made in place, but a change to the decision or its consequences must
be recorded in a superseding ADR. Implementation details deliberately deferred by
an ADR remain open; they are not implicit decisions.
