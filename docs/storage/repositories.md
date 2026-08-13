# Evidence repositories

Milestone 2 stores capture metadata and packet evidence behind inward-facing repository
contracts. Application and interface code depends on `jaws.ports`; Neo4j-specific Cypher
and record translation live in `jaws.storage`.

## CaptureRepository

`CaptureRepository` owns capture registration, canonical lookup, non-unique legacy-alias
lookup, chronological catalog listing, and lifecycle transitions. A transition supplies
the state the caller observed and fails on a conflict. Canonical identity and provenance
fields cannot change during a transition; only lifecycle timestamps, state, stored packet
count, and failure code advance.

Registration rejects duplicate canonical IDs. A legacy timestamp alias is intentionally
not unique because simultaneous captures must remain independently addressable.

## PacketRepository

`PacketRepository` accepts a bounded sequence whose records all name the requested active
capture. A non-empty batch is one transaction: it is either fully retained or contributes
no packets. Writes to missing or terminal captures fail. An empty append is a no-op.

Reads require an `ObservationWindow`, preserve its declared capture order, sort packets by
original observation time within each capture, and apply inclusive optional start/end
bounds. `read_all` is the explicit pooled-evidence operation and returns a deterministic
global evidence-time order, including packets whose capture catalog entry is unavailable.
The repository does not infer an imported capture's host perspective.

`PacketRecord` normalizes IPv4/IPv6 text, uses aware UTC datetimes, expresses missing
transport ports as `None`, and retains the existing payload text when present. The Neo4j
adapter maps missing ports back to the legacy numeric `0` property without creating a
`PORT` node.

## Implementations and schema guard

The deterministic in-memory repositories and Neo4j repositories run through the same
behavioral contract. `Neo4jRepositories.connect` validates the managed schema before it
exposes adapters; callers must migrate through `jaws-schema` or `initialize_schema` first.
The Neo4j implementation dual-writes version-2 and legacy capture properties described in
the [schema contract](neo4j-schema.md).

Repository errors distinguish duplicates, missing captures, optimistic state conflicts,
inactive captures, and unsupported schema state without exposing Neo4j exception types to
application code.

## EnrichmentRepository

`EnrichmentRepository` owns the IP inventory count, deterministic pending-address order,
provider records, researcher annotations, and targeted legacy-`Unknown` cleanup. Provider
records distinguish successful, not-applicable, not-found, transient-failure, and
permanent-failure outcomes. Only transient failures remain pending after a recorded
attempt. Provider fields and researcher annotations are never merged into one record.

`list_metadata` is a compatibility read projection for organization, hostname, location,
and coordinates already attached to IP entities. It may include local-host or legacy
ownership labels with no provider provenance, so it is never returned as or promoted into
an `EnrichmentRecord`.

The compatibility adapter continues to write `OWNERSHIP`, `HOSTNAME`, `LOCATION`, and
`COORDINATES`, while also storing provider ID/revision, acquisition time, outcome, ASN,
confidence, and failure code. Re-enrichment replaces prior provider ownership instead of
accumulating contradictory provider organizations.

## ProfileRepository

`ProfileRepository` atomically replaces all profiles in one explicit observation scope,
reads a scope, reads earlier concrete scopes by capture evidence time, lists computed
scopes, applies complete tri-state outlier verdict batches, and deletes an exact previously
planned set of whole profile scopes. Missing scope/entity/profile validation occurs before
destructive writes in the same transaction. Exact-scope deletion validates identity,
legacy alias, compute time, record count, and status so a stale retention plan fails without
deleting a replacement.

Applied retention supplies a minimal `AuditEvent`; validation, exact-scope deletion, and
event creation share one transaction. Dry-run and a stale plan write no audit record.

Pooled `all` is represented by `scope_pooled_all` and returns no history. Unstamped legacy
profiles are retained in `scope_legacy_unstamped` with `legacy_quarantined` status.
Version-aware writers require a computation timestamp and canonical `PROFILE_KEY`; readers
may expose quarantined/unversioned records without inventing missing provenance.

## InspectionRepository

`InspectionRepository` owns optimized, read-only projections used by overview and endpoint
drill-down interfaces. `recent_profiles` selects the most recently computed profile set,
applies an explicit computation-time lower bound, ranks by total traffic, and enforces a
row limit. `inspect` returns one IP's latest profile, chronological concrete-scope history,
all-session packet/peer totals, bounded peer aggregates, and a bounded newest-packet sample.

The projection retains legacy directionality: outbound means the inspected endpoint is the
packet source. Peer service ports use the low-side flow heuristic while distinct high-side
ports are exposed only as an ephemeral-port churn count. Metadata attached to profiles wins;
entity metadata is a display-only fallback. Cypher and Neo4j value decoding remain inside
the storage adapter, while MCP owns only compatibility serialization and cloud-host hints.

The in-memory implementation composes `ProfileRepository`, `PacketRepository`, and
`EnrichmentRepository`. Both implementations run through the same behavioral contract,
including limits, missing endpoints, metadata fallback, history order, port roles, and
directional byte counts.

## Retention service

`RetentionPolicy` declares independent rules for raw packets, capture metadata, profile
sets, experiment indexes, and external artifact bundles. Every resource must appear exactly
once. The safe default is `keep_all`; this milestone implements finite `keep_latest` only
for nonquarantined profile sets and rejects unsupported finite rules.

`RetentionService.plan` and `dry_run` only read profile summaries. Their `RetentionPlan`
names every retained, deleted, and protected profile scope and the exact profile-row count
proposed for deletion. `apply` recalculates the policy and delegates exact-scope validation
plus deletion to one repository transaction. Quarantined legacy scopes are protected and
do not consume the finite profile allowance.

Operators can inspect the JSON plan independently:

```bash
jaws-retention dry-run --retain-profiles 20
jaws-retention apply --retain-profiles 20
```

The legacy `jaws-compute --retain-profiles` flag constructs and applies the same complete
policy after a successful profile replacement, preserving its result envelope during the
compatibility period. Raw packet and capture history remain explicitly `keep_all`.

## Evidence export and import

`EvidenceRepository` exposes an exact typed snapshot of the managed evidence contract:
captures, observation scopes, packets, IP entities and all ownership organizations,
provider enrichments, researcher annotations, and current or legacy endpoint profiles.
The snapshot also carries the ordered managed migration version, names, and checksums.
`EvidenceTransferService` builds and verifies the portable bundle independently of Neo4j
and file-system details.

Create and validate a bundle:

```bash
jaws-evidence export /secure/backups/jaws-evidence.json --database neo4j
jaws-evidence validate /secure/backups/jaws-evidence.json
```

The export is canonical JSON with per-section record counts/checksums and a whole-content
checksum. Publication is atomic and refuses to overwrite an existing path. Validation is
offline and does not load JAWS settings or contact Neo4j.

Restore only into a fresh database after applying the matching managed migrations:

```bash
jaws-schema migrate --database jaws-restored
jaws-evidence import /secure/backups/jaws-evidence.json \
  --database jaws-restored --dry-run
jaws-evidence import /secure/backups/jaws-evidence.json \
  --database jaws-restored
```

Dry-run does not write. Apply repeats exact schema-provenance and empty-target checks inside
the restore transaction; only the two schema-created empty system scopes are ignored, so
any other node makes the target nonempty. A populated target or changed plan fails closed.
Statement batches share one transaction, so a failure does not leave a partial import.
After commit, the service re-exports and compares the content checksum before reporting
success.

Evidence bundles include retained packet payload text and enriched metadata. Treat them as
sensitive research evidence, restrict their filesystem and transport access, and do not
commit them to the source repository. Application secrets are not part of the managed
evidence snapshot. The bundle is not a general Neo4j dump: unmanaged labels, properties,
and relationships require a separate database-native backup if they must be preserved.

## AdministrationRepository

`AdministrationRepository.plan` returns the exact database, ordered schema migration
provenance, categorized evidence/unclassified node counts, and relationship count. The
canonical digest over that complete value is an optimistic lock and part of the required
human confirmation. Planning is read-only.

`erase` recalculates the plan inside the write transaction. It either deletes every
non-administrative node and its relationships, restores the schema-owned pooled/quarantine
scopes, and creates one minimal audit event, or changes nothing. Migration history, prior
audit events, and schema objects remain. `audit_events` returns bounded newest-first
metadata; it never returns evidence or settings.

```bash
jaws-admin plan --database disposable-name
jaws-admin erase --database disposable-name \
  --confirm 'ERASE disposable-name <digest-from-plan>'
jaws-admin audit --database disposable-name
```

There is no default destructive target. `jaws-utils --drop` is rejected, and MCP does not
register an administration tool.
