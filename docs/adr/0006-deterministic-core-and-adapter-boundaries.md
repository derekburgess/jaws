# ADR-0006: Keep the deterministic core independent of interfaces, storage, and agents

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0002](0002-analytical-axes-and-research-operations.md), [ADR-0005](0005-evidence-store-and-portable-experiment-records.md) |

## Context

The current CLI modules contain domain and storage behavior, while the MCP server
mostly invokes those CLIs as subprocesses and also owns direct Cypher queries.
This duplicates contracts, makes MCP behavior dependent on rendered command
output, and prevents benchmarks from calling the same analytical path through a
stable Python API.

An optional research agent adds another interface and a stronger privilege
boundary. If the agent framework, MCP, Neo4j, or CLI becomes the core, JAWS cannot
test rankings independently, compare adapters reliably, or replace infrastructure
without changing analytical behavior.

## Decision

JAWS uses inward-facing domain and application boundaries:

```text
CLI / MCP / benchmarks / notebooks / optional agent
                       |
              application services
                       |
         domain records and dependency protocols
                       ^
      storage, capture, enrichment, embedding adapters
```

The deterministic Python core:

- defines validated domain records and the seven research operations;
- depends on protocols rather than concrete Neo4j, model-provider, CLI, MCP, or
  agent-framework packages;
- accepts declared representations and references instead of querying storage
  from inside rankers;
- produces scores, deterministic ranks, flags, contributions, and metric vectors
  as structured data; and
- permits storage, embedding, enrichment, clocks, and ID generation to be replaced
  by deterministic fakes.

CLI, MCP, benchmark, notebook, and agent packages validate external input, call
the same application services, and serialize returned domain results. They do not
own alternative scoring logic. MCP v2 does not use CLI subprocesses as its
application API and does not embed direct detector Cypher.

External providers may themselves be remote, version-changing, or stochastic.
Their adapters must make inputs, outputs, versions, and unavailable states
explicit so downstream scoring and evaluation can be reproduced from retained
records. “Deterministic core” does not pretend that an unversioned external API is
deterministic.

Agents remain optional orchestration clients. They may orient to prior results,
propose hypotheses, configure bounded registered experiments, and interpret
deterministic observations. They do not calculate rewards, define ground truth,
or gain capture, shell, database-deletion, or unrestricted network privileges by
default. Agent execution, analysis execution, and capture privileges remain
separate. Whether NOOA or another framework is used is deferred to the Milestone
9 ADR and sandboxed spike.

## Alternatives considered

### Keep the CLI as the application implementation

This preserves working code and offers convenient subprocess isolation, but
rendered text/JSON envelopes are a brittle internal API and force every new
interface to duplicate orchestration.

### Make MCP the canonical core interface

This supports model clients directly, but couples tests and local research to a
transport protocol and leaves non-MCP Python consumers without a direct contract.

### Make Neo4j queries the domain API

This offers expressive investigation and fewer repository abstractions, but
binds research semantics to one schema and makes isolated deterministic tests
difficult.

### Build the core around an agent framework

This accelerates autonomous workflows but makes ranking reproducibility,
privilege isolation, and model portability depend on research software outside
the detector's analytical contract.

## Consequences

### Benefits

- Correctness and ranker tests run without credentials, Neo4j, MCP, or model
  downloads when those dependencies are not logically required.
- Every supported interface exercises the same analytical implementation.
- Storage and provider changes can be tested through contracts and parity
  artifacts.
- Optional agents can evolve or be removed without changing JAWS core.

### Costs and limitations

- Extracting services and protocols requires substantial staged refactoring.
- Adapter contracts and dependency injection add explicit plumbing.
- Some high-performance operations may require carefully designed bulk interfaces
  to avoid inefficient abstraction boundaries.
- Reproducibility still depends on capturing provider results and numerical
  environment details, not architecture alone.

### Follow-on constraints

- Dependencies flow inward; domain modules do not import infrastructure or
  interface packages.
- Plotting and report generation consume retained results and cannot mutate
  rankings.
- CLI and MCP parity tests compare typed envelopes and analytical outputs.
- Destructive or privileged operations require separate capabilities and policy,
  not merely an adapter method exposed to every caller.

## Benchmark impact

Benchmark runners invoke application services directly. Adapter parity tests prove
that equivalent CLI and MCP requests resolve to the same specifications and
analytical results. Lightweight correctness tests must remain runnable offline;
provider-dependent benchmark tracks declare and record their external inputs.

The `legacy_2_0` path preserves Benchmark 0 behavior while implementation moves
behind the new boundaries.

## Migration

Milestone 1 establishes package boundaries, protocols, and dependency groups.
Milestones 2 through 6 extract storage and analytical services behind them.
Legacy CLIs remain thin compatibility adapters until parity tests and migration
documentation pass. MCP is rebuilt over completed services in Milestone 8 rather
than defining those services itself.

## Reversal conditions

Revisit a boundary if profiling demonstrates unacceptable performance or if a
required research operation cannot be expressed without loss of semantics. A
replacement must retain direct testability, one analytical path across
interfaces, deterministic reward calculation, and agent privilege separation.

## References

- [Target architecture and layering rules](../../IMPLEMENTATION_PLAN.md#target-architecture)
- [Interfaces](../../README.md#interfaces)
