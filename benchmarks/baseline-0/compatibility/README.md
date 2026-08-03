# Benchmark 0 compatibility inventories

These machine-readable records complete the adapter and storage portion of Benchmark 0.
They describe the frozen subject revision; they do not propose the Milestone 1 CLI
or Milestone 2 graph design.

## CLI JSON contract

`cli-contract.json` retains 7 deterministic invocations:
3 successes, 3 handled application/configuration failures,
and 1 argparse validation failure.

| Case | Entry point | Category | Exit | Stdout |
| --- | --- | --- | ---: | --- |
| `capture-list-success` | `jaws-capture` | success | 0 | json_envelope |
| `compute-success` | `jaws-compute` | success | 0 | json_envelope |
| `rank-success` | `jaws-finder` | success | 0 | json_envelope |
| `compute-unknown-session` | `jaws-compute` | handled_error | 0 | json_envelope |
| `capture-missing-file` | `jaws-capture` | handled_error | 0 | json_envelope |
| `compute-neo4j-unavailable` | `jaws-compute` | handled_error | 0 | json_envelope |
| `capture-invalid-duration` | `jaws-capture` | argument_error | 2 | no_stdout |

The observed contract deliberately records that Reporter-handled failures emit
`ok=false` JSON while retaining exit status 0. Argument parsing is the exception:
it emits stderr text, no JSON, and exits 2. This is baseline evidence, not an
endorsement; typed CLI semantics are decided in Milestone 1.

## Neo4j graph schema

`neo4j-schema.json` is derived from every Cypher-bearing source location at the
subject revision. It records:

- 6 node labels and 40 observed node properties
- 5 relationship types and no relationship properties
- 3 uniqueness constraints and 5 range indexes
- 44 Cypher source locations with source digests
- property-based joins, seed data, lifecycle behavior, and known limitations

The current schema is initialized opportunistically before capture, has no schema
version or migration ledger, and can continue after partial initialization errors.
Those observations establish the migration boundary for Milestone 2.

## Integrity

Both records name the detector subject and their own committed collector revision.
The root Benchmark 0 manifest inventories these files, and the root checksum file
covers them. The existing bundle validator also scans them for credential patterns.
