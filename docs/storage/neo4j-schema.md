# Neo4j schema contract

This document separates the unversioned starting graph from the first managed target. The
`schema_version` field in the frozen Benchmark 0 JSON identifies that artifact's document
format; it was not stored in Neo4j and is not a database migration version.

## Starting revision: unversioned legacy graph

The source of truth for the complete observed starting state is the
[frozen Benchmark 0 inventory](../../benchmarks/baseline-0/compatibility/neo4j-schema.json),
derived from all 44 Cypher-bearing source locations at revision `0b68a8c`. Its contract is:

| Label | Logical identity | Properties |
| --- | --- | --- |
| `CAPTURE` | `CAPTURE_ID` | `CAPTURE_ID`, `PACKETS`, `SOURCE`, `STARTED` |
| `ENDPOINT` | `IP_ADDRESS`, `CAPTURE_ID` | `BYTES_IN`, `BYTES_OUT`, `CAPTURE_ID`, `EMBEDDING`, `ENDPOINT_TYPE`, `HOSTNAME`, `INTERVAL_CV`, `INTERVAL_MEAN`, `IN_PEERS`, `IN_PORTS`, `IP_ADDRESS`, `LOCATION`, `ORGANIZATION`, `OUTLIER`, `OUT_PEERS`, `OUT_PORTS`, `PACKETS_IN`, `PACKETS_OUT`, `PROTOCOLS`, `TIMESTAMP` |
| `IP_ADDRESS` | `IP_ADDRESS` | `COORDINATES`, `HOSTNAME`, `IP_ADDRESS`, `LOCATION` |
| `ORGANIZATION` | `ORGANIZATION` | `ORGANIZATION` |
| `PACKET` | No enforced identity | `CAPTURE_ID`, `DST_IP`, `DST_PORT`, `PAYLOAD`, `PROTOCOL`, `SIZE`, `SRC_IP`, `SRC_PORT`, `TIMESTAMP` |
| `PORT` | `PORT`, `IP_ADDRESS` | `IP_ADDRESS`, `PORT` |

| Relationship | Direction | Properties |
| --- | --- | --- |
| `OWNERSHIP` | `ORGANIZATION` → `IP_ADDRESS` | None |
| `PORT` | `IP_ADDRESS` → `PORT` | None |
| `PROFILE` | `IP_ADDRESS` → `ENDPOINT` | None |
| `RECEIVED` | `PACKET` → `PORT` | None |
| `SENT` | `PORT` → `PACKET` | None |

The graph also relies on implicit property joins from packet capture/IP fields and
endpoint IP fields. It has no foreign-key, property-existence, relationship, or property-
type constraints. `ENDPOINT` and `PORT` logical identities are indexed but not unique;
legacy profiles may have no `CAPTURE_ID`; and `OUTLIER` is tri-state through absence.

### Legacy constraints

| Name | Label | Properties |
| --- | --- | --- |
| `capture_id_unique` | `CAPTURE` | `CAPTURE_ID` |
| `ip_address_unique` | `IP_ADDRESS` | `IP_ADDRESS` |
| `organization_unique` | `ORGANIZATION` | `ORGANIZATION` |

### Legacy explicit indexes

| Name | Label | Properties |
| --- | --- | --- |
| `endpoint_capture_index` | `ENDPOINT` | `IP_ADDRESS`, `CAPTURE_ID` |
| `endpoint_ip_index` | `ENDPOINT` | `IP_ADDRESS` |
| `packet_capture_index` | `PACKET` | `CAPTURE_ID` |
| `packet_timestamp_index` | `PACKET` | `TIMESTAMP` |
| `port_composite_index` | `PORT` | `PORT`, `IP_ADDRESS` |

## Managed target: database schema version 1

Version 1 adopts every legacy label, property, relationship, constraint, and index above
without renaming or rewriting data. It adds this administrative node:

| Label | Identity | Required properties | Purpose |
| --- | --- | --- | --- |
| `JAWS_SCHEMA_MIGRATION` | `VERSION` | `VERSION` integer, `NAME` string, `CHECKSUM` SHA-256 text, `APPLIED_AT` Neo4j datetime | Immutable record of one applied ordered migration |

It also adds uniqueness constraint `jaws_schema_migration_version_unique` on
`JAWS_SCHEMA_MIGRATION.VERSION`. A database is current at version 1 only when the applied
record's name and checksum match the registry and all four constraints plus all five
explicit indexes match their declared names, labels, and ordered properties.

Extra unrelated schema objects do not invalidate version 1. A missing or drifted required
object does. Applying version 1 is non-destructive and does not justify deleting unexpected
objects automatically.

The local-host `YOU ARE HERE` organization and its `OWNERSHIP` relationship are runtime
seed data because the IP is invocation-specific. They are idempotently initialized after
schema migration and are not part of the migration checksum.

## Operations

The installed `jaws-schema` command uses the same Neo4j connection settings as the other
JAWS commands and accepts the existing `--database` runtime override:

```text
jaws-schema status [--database NAME]
jaws-schema validate [--database NAME]
jaws-schema dry-run [--database NAME]
jaws-schema migrate [--database NAME]
```

`status`, `validate`, and `dry-run` do not write. `validate` exits nonzero for pending,
unknown, checksum-drifted, or schema-drifted migrations. `migrate` applies pending
idempotent statements, waits for indexes, validates required objects, and only then writes
the applied record. A failure before the record is recoverable by retrying after the cause
is corrected.

No operation reads a database name from a new environment setting. `--database` remains a
runtime argument; URI, username, and password retain their existing settings behavior.

## Later Milestone 2 versions

Version 1 establishes ownership, not the final evidence redesign. Later migrations own
collision-resistant capture identity, lifecycle/provenance fields, explicit observation
scope, profile uniqueness, legacy aliases, and legacy-data quarantine. Each will add its
target contract here before implementation, state backup and rollback behavior, and retain
Benchmark 0 compatibility until repository and interface parity gates pass.
