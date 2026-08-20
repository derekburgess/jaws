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
| `CAPTURE_FILTER` | Exact declared capture/display filter provenance; combined filters carry explicit `capture=`/`display=` prefixes |
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
| `ENDPOINT.EMBEDDING_PROVIDER_ID`, `MODEL_ID`, `MODEL_REVISION`, `MODEL_REVISION_EXACT` | Provider/model identity and whether the retained revision is immutable |
| `ENDPOINT.EMBEDDING_DIMENSIONS`, `EMBEDDING_NORMALIZATION`, `EMBEDDING_BATCH_SIZE`, `EMBEDDING_DEVICE` | Exact embedding execution shape and runtime metadata |
| `ENDPOINT.EMBEDDING_INPUT_DIGEST` | Canonical digest joining the stored profile/vector to its exact rendered input text |

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

## Managed target: database schema version 5

Version 5 adds a minimal discovery index for experiment specifications and their append-
only execution runs. It is additive, creates no experiment data, and retains every prior
schema object. Canonical specifications, results, findings, metrics, logs, and other run
payloads remain external portable artifacts rather than graph properties.

| Label/property | Contract |
| --- | --- |
| `JAWS_EXPERIMENT.EXPERIMENT_ID` | Canonical specification digest and unique experiment identity |
| `JAWS_EXPERIMENT.SPECIFICATION_SHA256` | Lowercase SHA-256 of the immutable semantic specification; must equal `EXPERIMENT_ID` |
| `JAWS_EXPERIMENT_RUN.RUN_ID` | Unique append-only execution-attempt identity |
| `STATE` | `created`, `running`, `succeeded`, `failed`, or `cancelled` |
| `CREATED_AT`, `STARTED_AT`, `ENDED_AT` | Aware lifecycle datetimes; required according to state |
| `ARTIFACT_URI`, `ARTIFACT_SHA256` | Paired secret-free URI and checksum for a finalized canonical run artifact |
| `FAILURE_CODE` | Non-secret machine-readable code for a failed run only |

Every run has exactly one `(:JAWS_EXPERIMENT_RUN)-[:RUN_OF]->(:JAWS_EXPERIMENT)`
relationship. A rerun may point to one terminal prior run of the same experiment through
`[:SUPERSEDES]`; supersession never deletes or rewrites the prior run. Neo4j lifecycle
transitions use the caller's expected state as an optimistic lock, and identity metadata
cannot change during a transition.

### Version-5 constraints and indexes

| Kind | Name | Label/properties |
| --- | --- | --- |
| Uniqueness constraint | `jaws_experiment_id_unique` | `JAWS_EXPERIMENT(EXPERIMENT_ID)` |
| Uniqueness constraint | `jaws_experiment_run_id_unique` | `JAWS_EXPERIMENT_RUN(RUN_ID)` |
| Range index | `jaws_experiment_run_state_created_index` | `JAWS_EXPERIMENT_RUN(STATE, CREATED_AT)` |
| Range index | `jaws_experiment_run_artifact_uri_index` | `JAWS_EXPERIMENT_RUN(ARTIFACT_URI)` |

The authority split and reconstruction requirement are detailed in
[ADR-0005](../adr/0005-evidence-store-and-portable-experiment-records.md).

## Managed target: database schema version 6

Version 6 adds the optional, reconstructable discovery index for ranked findings. A finding
batch may be published only for a `succeeded` run and must name that run's exact canonical
artifact checksum. Publication records the batch digest and count on the run, so an
identical retry is a no-op and different later content fails without partially rewriting
the graph.

| Label/property | Contract |
| --- | --- |
| `JAWS_FINDING_INDEX.FINDING_ID` | Unique finding identity from the canonical run artifact |
| `RUN_ID` | Denormalized run identity for indexed run/rank lookup |
| `ENTITY_ID` | Stable ranked-entity identity; no entity type or perspective is inferred |
| `RANK` | Positive, unique, contiguous one-based rank within the published run batch |
| `SCORE` | Finite ranking score |
| `SCORE_DIRECTION` | `higher_is_more_anomalous` or `lower_is_more_anomalous` |
| `OUTLIER_STATUS` | `outlier`, `inlier`, or `not_scored`; never a malicious/benign verdict |
| `JAWS_EXPERIMENT_RUN.FINDING_INDEX_SHA256` | Canonical digest of the complete artifact-bound index batch after publication |
| `JAWS_EXPERIMENT_RUN.FINDING_COUNT` | Exact number of indexed rows, including zero |

Every indexed finding has exactly one
`(:JAWS_FINDING_INDEX)-[:IN_RUN]->(:JAWS_EXPERIMENT_RUN)` relationship. The graph does not
copy finding explanations, contributions, evidence pointers, feature values, metrics,
labels, or result payloads. Those remain authoritative only in the canonical external run
artifact, from which this index must be reconstructable.

### Version-6 constraints and indexes

| Kind | Name | Label/properties |
| --- | --- | --- |
| Uniqueness constraint | `jaws_finding_index_id_unique` | `JAWS_FINDING_INDEX(FINDING_ID)` |
| Range index | `jaws_finding_run_rank_index` | `JAWS_FINDING_INDEX(RUN_ID, RANK)` |
| Range index | `jaws_finding_entity_index` | `JAWS_FINDING_INDEX(ENTITY_ID)` |

## Managed target: database schema version 7

Version 7 adds typed descriptive source provenance for new PCAP imports. It retains every
earlier property and schema object; no legacy capture is backfilled with guessed metadata.

| `CAPTURE` property | Contract |
| --- | --- |
| `SOURCE_FILE_NAME` | Original filename reported by the source adapter |
| `SOURCE_SIZE_BYTES` | Nonnegative file size at ingest time |
| `SOURCE_LOCATOR` | Optional original path or dataset locator; descriptive, never capture identity |
| `SOURCE_LOCATOR_PORTABLE` | Boolean declaring whether the locator is portable; local CLI paths are always `false` |

`CONTENT_SHA256` remains the portable content identity. Evidence bundles carry the typed
metadata and retain format-version-1 compatibility with bundles created before these
optional fields existed. The additive range index
`capture_source_file_name_index` covers `CAPTURE(SOURCE_FILE_NAME)`.

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

## Managed target and later versions

Version 6 completes the Milestone 2 managed schema; version 7 is the first Milestone 3
addition. Retention, administration, and portable managed-evidence export/import operate
over version 7. Experiment and finding index nodes are reconstructable discovery metadata
and are not copied into evidence bundles. Later versions must document their contracts
before implementation and retain Benchmark 0 compatibility until their parity gates pass.
