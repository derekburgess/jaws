# ADR-0021: Major release and experimental agent laboratory

## Status

Accepted

## Context

The research-workbench rollout preserves the legacy command names and the named
`legacy_2_0` ranker, but it also introduces deliberately versioned contracts that a 2.0
client cannot consume unchanged. MCP v2 replaces pipeline-oriented tools with bounded
research operations and an asynchronous run lifecycle. Portable experiment bundles,
evidence bundles, database migration records, and container provenance are now explicit
contracts rather than incidental output. Treating those changes as a minor release would
understate the migration required of MCP clients and artifact consumers.

Milestone 9 also produced a useful OHEO laboratory. It is isolated and dependency-free,
but it is an orchestration experiment rather than part of the deterministic analytical
core. Its maturity and authority should not be confused with the release status of the
core workbench.

## Decision

Release the integrated workbench as JAWS **3.0.0**. Package, CPU/GPU/sensor/MCP image
labels, Compose defaults, and release records use that package version. Contract versions
remain independently meaningful: MCP and its research API are `2.0.0`; experiment,
evidence, benchmark, runtime, and agent trace formats remain `1.0.0`; the managed Neo4j
schema is migration version 7. A package-major bump does not rewrite stable schema
identities.

Ship `jaws_lab` and the `agent-lab` installation/container profile as an explicitly
experimental extra. It is not installed by the numerical core, imported by deterministic
services, granted network or capture capabilities, or allowed to calculate evaluation
metrics. Removing the extra leaves CLI, MCP, storage, experiments, and benchmarks intact.

The first candidate is named `3.0.0-rc1` in release records. The Python distribution
version stays PEP 440 final-shaped at `3.0.0`; no final tag or public release is created
until the candidate evidence is reviewed.

## Consequences

2.0 command users retain compatibility adapters but should migrate to `jaws-research`,
the MCP v2 tool set, managed schema migration, and verified portable bundles. MCP clients
must update tool names and long-running-job handling. Operators can evaluate the agent
laboratory without adding an agent framework or widening core permissions. Release
qualification must publish a compatibility matrix, benchmark checksums, container
digests, known limitations, and explicit unavailable-resource decisions alongside the
reviewed code revision.
