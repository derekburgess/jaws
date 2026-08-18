# ADR-0012: Use ordered, checksummed Neo4j schema migrations

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-10 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0005](0005-evidence-store-and-portable-experiment-records.md), [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#milestone-2--versioned-evidence-storage-and-migrations), [schema contract](../storage/neo4j-schema.md) |

## Context

The starting JAWS code creates constraints and indexes opportunistically before capture,
collects statement failures as warnings, and carries no database schema version. The
Benchmark 0 inventory found three uniqueness constraints, five explicit range indexes,
six labels, 40 node properties, five relationship types, and no migration history. A
partially initialized graph is therefore possible, and neither an operator nor a service
can prove which schema contract it is using.

Milestone 2 must support both an empty database and graphs created by the starting
revision. The first transition must not rename evidence, rewrite stored values, or make an
unverified assumption about legacy data.

## Decision

JAWS owns Neo4j schema through an ordered, immutable migration registry in
`jaws.storage.migrations`. Versions are positive contiguous integers. Each applied
migration is represented by one `JAWS_SCHEMA_MIGRATION` node with `VERSION`, `NAME`,
`CHECKSUM`, and `APPLIED_AT`; a uniqueness constraint protects `VERSION`.

The checksum covers the version, name, Cypher statements, required schema objects, and
rollback policy. An applied name or checksum that differs from the registry is a blocking
validation error. Unknown applied versions and non-contiguous history are also blocking.
Changing an applied migration in place is prohibited; corrections require a new version.

Each migration declares the named constraints and indexes that must exist after it runs.
Statements must be idempotent. The runner waits for indexes, validates the required live
schema, and only then records the version in a write transaction. Neo4j may commit schema
commands independently, so an interrupted migration can leave unrecorded schema objects;
retrying the same idempotent migration is the recovery path.

`status` and `validate` are read-only. `dry-run` reads status and emits the exact pending
ordered statements without executing them. `migrate` is the only operation that applies
and records migrations. Driver creation remains lazy, so importing the package needs no
credential or optional Neo4j package.

Version 1 adopts the existing Benchmark 0 constraints and indexes and adds only the
migration-version constraint and record. It does not rewrite evidence. Runtime-local seed
data (`YOU ARE HERE`) remains initialization data rather than schema history.

## Alternatives considered

### Continue opportunistic initialization

This retains a small implementation but cannot distinguish a current graph from a partial
or drifted graph, cannot order data changes, and provides no upgrade evidence.

### Add a migration framework dependency

A general framework could provide a richer command surface. None is already a direct
dependency, and JAWS currently needs a small Neo4j-specific ordered registry. Introducing
one before repository contracts stabilize would enlarge the runtime and compatibility
surface without removing the need for JAWS-specific validation.

### Store only one mutable current-version value

This is easy to query but loses per-transition names, checksums, timestamps, and drift
detection. It also makes interrupted and manually edited history harder to diagnose.

### Infer version solely from live constraints and indexes

Inference helps validate state but cannot prove which data migrations ran. Named schema
objects are validation evidence, not a replacement for ordered history.

## Consequences

### Benefits

- Fresh and legacy graphs enter the same deterministic upgrade path.
- Status and dry-run are safe to use before any write.
- Applied migration meaning is auditable and protected against editing in place.
- Partial schema application is recoverable through idempotent retry.

### Costs and limitations

- Neo4j cannot make a sequence of every schema command and data update one atomic unit.
- Each migration needs explicit required-schema and rollback declarations.
- The migration ledger proves JAWS migration history, not that no administrator changed
  unrelated graph objects.

### Follow-on constraints

- A destructive or irreversible migration requires a verified metadata/data export before
  execution and a migration-specific confirmation boundary. Implemented migration plans
  expose their safety classification, protected versions, and pre-migration schema digest;
  `jaws-schema migrate --backup` accepts proof only after a portable bundle's internal
  checksums, schema provenance, and complete live-evidence checksum match. Version 1 is the
  sole grandfathered case because it adopts schema objects without mutating evidence and
  predates the managed evidence format.
- Repository adapters must validate a supported schema version before writes.
- Migration integration tests run only against an explicitly designated disposable test
  database, never an implicitly selected research database.
- Schema status may tolerate unrelated extra indexes, but may not tolerate a required
  object's name, label, or property drift.

## Benchmark impact

Version 1 has no analytical impact: labels, evidence properties, relationship semantics,
queries, rankings, reasons, and result envelopes remain unchanged. Benchmark 0 remains
the parity authority. Later migrations that alter evidence identity or observation scope
must add migration fixtures and rerun the relevant benchmark comparison.

## Migration

An empty database creates the eight legacy schema objects plus the migration constraint,
then records version 1. A starting-revision database encounters the same statements with
`IF NOT EXISTS`, validates the existing objects, and records version 1 without changing
evidence. If validation fails, no version record is written and the mismatch must be
resolved before retry.

Version 1 has no automatic downgrade. On a legacy database, the runner cannot know whether
an object predated adoption; dropping it would damage the original contract. Restore a
verified backup to reverse adoption. Future reversible migrations must state and test
their downgrade operations; non-reversible ones must say so explicitly.

## Reversal conditions

Reconsider the mechanism if migrations require cross-database coordination, online
backfills, or operational locking that this registry cannot provide safely. A replacement
must preserve ordered history, immutable checksums, read-only planning, live validation,
legacy adoption, and explicit rollback metadata.

## References

- [Current and managed target schema](../storage/neo4j-schema.md)
- [Frozen Benchmark 0 graph inventory](../../benchmarks/baseline-0/compatibility/neo4j-schema.json)
