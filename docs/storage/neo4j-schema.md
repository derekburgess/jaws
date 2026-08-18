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

## Managed target: database schema version 2

Version 2 is additive and retains every version-1 object. New captures use opaque UUID4-
backed `CAPTURE_ID` values while legacy captures retain their original identity.

### Capture properties

| Property | Contract |
| --- | --- |
| `CAPTURE_ID` | Unique canonical identity; `cap_` plus UUID4 hex for new captures, preserved timestamp-shaped value for legacy captures |
| `LEGACY_CAPTURE_ID` | Human-readable timestamp alias for new captures; preserved original ID for migrated captures; indexed, not unique |
| `STATE` | `registered`, `running`, `importing`, `complete`, `partial`, `failed`, or `cancelled` |
| `SOURCE_KIND` | `live_interface`, `pcap_file`, or `legacy_unknown` |
| `SOURCE_NAME` | Interface, PCAP source name/path, or preserved legacy source |
| `CONTENT_SHA256` | Lowercase PCAP SHA-256 when available; absent for live/legacy evidence |
| `REGISTERED_AT`, `STARTED_AT`, `ENDED_AT` | Neo4j datetimes; unknown legacy end time remains absent |
| `PACKET_COUNT` | Nonnegative stored packet count; mirrors legacy `PACKETS` during compatibility |
| `PERSPECTIVE_IP` | Capture-host IP only when known; absent for imported/legacy evidence unless supplied explicitly |
| `CAPTURE_FILTER` | Capture/display filter when one was declared |
| `TOOL_VERSIONS_JSON` | Canonical JSON object of available tool versions |
| `FAILURE_CODE` | Non-secret machine-readable terminal failure code when applicable |

`SOURCE`, `STARTED`, and `PACKETS` remain dual-written compatibility fields. New code must
not use `LEGACY_CAPTURE_ID` or UUID lexicographic order as chronology.

Capture catalog/lifecycle and packet batch/read behavior is defined separately by the
[repository contract](repositories.md). Its Neo4j adapter owns the corresponding Cypher
and refuses writes unless the managed migration history and required schema are current.

### Observation scopes and profiles

| Label/property | Contract |
| --- | --- |
| `OBSERVATION_SCOPE.SCOPE_ID` | Unique explicit scope identity |
| `OBSERVATION_SCOPE.KIND` | `capture`, `pooled`, `custom`, or `legacy` |
| `OBSERVATION_SCOPE.CREATED_AT` | UTC creation datetime |
| `OBSERVATION_SCOPE.PERSPECTIVE_IP` | Optional known host perspective |
| `OBSERVATION_SCOPE.FILTERS` | Ordered filter strings |
| `ENDPOINT.SCOPE_ID` | Explicit profile scope; backfilled from non-null legacy `CAPTURE_ID` |
| `ENDPOINT.PROFILE_KEY` | Canonical digest of entity, scope, representation version, and optional complete model ID/revision pair |

The new relationship is
`(:OBSERVATION_SCOPE)-[:INCLUDES]->(:CAPTURE)`. Version 2 does not invent `PROFILE_KEY`
for legacy endpoints because representation/model provenance cannot be recovered reliably.

### Version-2 constraints and indexes

| Kind | Name | Label/properties |
| --- | --- | --- |
| Uniqueness constraint | `observation_scope_id_unique` | `OBSERVATION_SCOPE(SCOPE_ID)` |
| Uniqueness constraint | `endpoint_profile_key_unique` | `ENDPOINT(PROFILE_KEY)` when present |
| Range index | `capture_legacy_id_index` | `CAPTURE(LEGACY_CAPTURE_ID)` |
| Range index | `capture_state_index` | `CAPTURE(STATE)` |
| Range index | `endpoint_scope_index` | `ENDPOINT(SCOPE_ID)` |

Legacy backfill copies original IDs into `LEGACY_CAPTURE_ID`, classifies captures with a
final packet count as `complete` and those without one as `partial`, retains unknown source
kind explicitly, and creates `scope_` plus `CAPTURE_ID` for each legacy capture. Migration
markers bound the conditional rollback described by
[ADR-0013](../adr/0013-capture-identity-lifecycle-and-observation-scope.md).

## Managed target: database schema version 3

Version 3 adds provider enrichment provenance, separate researcher annotations, and
explicit legacy-profile semantics. It retains all version-1 and version-2 objects.

| Label/property | Contract |
| --- | --- |
| `IP_ADDRESS.ENRICHMENT_STATUS` | `succeeded`, `not_applicable`, `not_found`, `transient_failure`, or `permanent_failure` |
| `IP_ADDRESS.ENRICHED_AT` | Provider acquisition UTC datetime |
| `IP_ADDRESS.ENRICHMENT_PROVIDER_ID`, `ENRICHMENT_PROVIDER_REVISION` | Provider identity and revision reported by the adapter |
| `IP_ADDRESS.ENRICHMENT_ORGANIZATION`, `ENRICHMENT_ASN`, `ENRICHMENT_CONFIDENCE`, `ENRICHMENT_FAILURE_CODE` | Provider-derived metadata or explicit outcome details |
| `ENTITY_ANNOTATION.ANNOTATION_KEY` | Canonical unique entity/key identity for researcher-authored metadata |
| `ENDPOINT.PROFILE_STATUS` | `current`, `legacy_unversioned`, or `legacy_quarantined` |
| `ENDPOINT.OUTLIER_STATUS` | `outlier`, `inlier`, or `not_scored`; legacy `OUTLIER` remains dual-written |

`(:IP_ADDRESS)-[:ANNOTATED_WITH]->(:ENTITY_ANNOTATION)` keeps annotations separate from
provider ownership. `scope_pooled_all` has kind `pooled`. Unstamped legacy profiles move
to `scope_legacy_unstamped`, kind `legacy`, with `QUARANTINED=true`; no endpoint is deleted.
Capture-scoped legacy profiles remain unversioned because their representation/model
identity cannot be recovered.

### Version-3 constraints and indexes

| Kind | Name | Label/properties |
| --- | --- | --- |
| Uniqueness constraint | `entity_annotation_key_unique` | `ENTITY_ANNOTATION(ANNOTATION_KEY)` |
| Range index | `ip_enrichment_status_index` | `IP_ADDRESS(ENRICHMENT_STATUS)` |
| Range index | `endpoint_scope_computed_index` | `ENDPOINT(SCOPE_ID, TIMESTAMP)` |

The repository behavior and conditional rollback are detailed in
[ADR-0014](../adr/0014-enrichment-provenance-and-versioned-profile-sets.md).

## Managed target: database schema version 4

Version 4 adds durable administrative audit metadata. It changes no managed evidence node
or relationship and retains every version-1 through version-3 schema object.

| Label/property | Contract |
| --- | --- |
| `JAWS_AUDIT_EVENT.EVENT_ID` | Opaque `audit_` identity, unique when present |
| `OPERATION`, `TARGET_DATABASE` | Guarded operation and exact database target |
| `OCCURRED_AT` | Aware UTC datetime |
| `ACTOR`, `INTERFACE` | Non-secret runtime attribution; not evidence or credentials |
| `PLAN_DIGEST` | SHA-256 of the complete exact operation plan |
| `AFFECTED_RECORDS` | Planned/deleted record count |

Audit nodes are administrative history: evidence export and erasure exclude them. They may
not contain packet payloads, settings, credentials, or copied evidence properties.

### Version-4 constraints and indexes

| Kind | Name | Label/properties |
| --- | --- | --- |
| Uniqueness constraint | `jaws_audit_event_id_unique` | `JAWS_AUDIT_EVENT(EVENT_ID)` |
| Range index | `jaws_audit_occurred_at_index` | `JAWS_AUDIT_EVENT(OCCURRED_AT)` |
| Range index | `jaws_audit_operation_target_index` | `JAWS_AUDIT_EVENT(OPERATION, TARGET_DATABASE)` |

The guarded operation and audit policy are detailed in
[ADR-0017](../adr/0017-guarded-administration-and-payload-free-audit.md).

## Operations

The installed `jaws-schema` command uses the same Neo4j connection settings as the other
JAWS commands and accepts the existing `--database` runtime override:

```text
jaws-schema status [--database NAME]
jaws-schema validate [--database NAME]
jaws-schema dry-run [--database NAME]
jaws-schema migrate [--database NAME] [--backup EVIDENCE_BUNDLE]
```

`status`, `validate`, and `dry-run` do not write. `validate` exits nonzero for pending,
unknown, checksum-drifted, or schema-drifted migrations. `migrate` applies pending
idempotent statements, waits for indexes, validates required objects, and only then writes
the applied record. A failure before the record is recoverable by retrying after the cause
is corrected.

Every migration declares an evidence-safety classification. `dry-run` includes
`backup_required_versions` and the canonical `source_schema_digest`. Additive migrations
need no proof. Before a destructive or irreversible migration, create a portable evidence
bundle from the source schema and pass it to `migrate --backup`. The command revalidates the
bundle's internal checksums and compares its schema and whole evidence-content checksum to
the live database before any migration statement runs. A missing, malformed, schema-
mismatched, or stale bundle fails without applying the protected migration.

```text
jaws-evidence export /secure/backups/pre-migration.json --database NAME
jaws-schema dry-run --database NAME
jaws-schema migrate --database NAME --backup /secure/backups/pre-migration.json
```

Evidence export accepts a valid applied migration prefix when newer migrations are pending,
which allows the backup to be created after installing new code but before upgrading the
database. Other repository bundles remain unavailable until the schema is current.

No operation reads a database name from a new environment setting. `--database` remains a
runtime argument; URI, username, and password retain their existing settings behavior.

Whole-evidence administration is a separate, human-only interface and always requires an
exact target:

```text
jaws-admin plan --database NAME
jaws-admin erase --database NAME --confirm 'ERASE NAME PLAN_DIGEST'
jaws-admin audit --database NAME [--limit N]
```

The plan reports exact evidence/unclassified node counts, relationship count, schema
provenance, digest, and confirmation. Erase repeats that plan inside one transaction,
preserves schema/audit nodes, and fails closed if anything changed.

## Later Milestone 2 versions

Versions 1–4 do not complete finding indexes, experiment indexes, or removal of every
remaining interface-owned Cypher query. Retention, administration, and portable managed-
evidence export/import operate over version 4. Later versions must document their contracts
before implementation and retain Benchmark 0 compatibility until their parity gates pass.
