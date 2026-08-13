# ADR-0017: Guard destructive administration with exact plans and payload-free audit

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-13 |
| Decision owners | Project maintainers |
| Supersedes | Legacy `jaws-utils --drop` and MCP `drop_database` behavior |
| Superseded by | None |
| Related | [ADR-0012](0012-ordered-neo4j-schema-migrations.md), [ADR-0015](0015-declared-retention-plan-before-apply.md), [ADR-0016](0016-portable-evidence-bundles-and-empty-target-import.md), [repository contract](../storage/repositories.md) |

## Context

The legacy utility defaulted its drop target to the configured research database. Human
mode asked only for the database name, while non-TTY agent mode skipped confirmation and
MCP exposed the operation as a generic tool. Its whole-graph delete also erased schema-
migration history. Retention had exact stale-plan protection but no durable record of an
applied deletion.

Destructive work must remain possible for a local operator without turning an analysis
interface into an administrative capability. Confirmation must describe what will be
deleted, not merely repeat a target name that may have changed since inspection. Audit
metadata must survive evidence erasure while never copying packet payloads or credentials.

## Decision

`jaws-admin` is the sole whole-evidence erasure interface. It is human-operated and is not
registered as an MCP tool. Both `plan` and `erase` require `--database`; destructive
administration has no configured default target.

An `AdministrationPlan` binds the operation to the exact database, ordered schema
provenance, categorized non-administrative node counts, relationship count, and a canonical
SHA-256 digest. `plan` emits the only accepted confirmation form:

```text
ERASE <database> <plan-digest>
```

`erase` requires that string byte-for-byte, then re-derives and compares the complete plan
inside the write transaction. A changed target fails without deletion or audit. A matching
transaction deletes all evidence and unexpected non-administrative nodes, restores the two
schema-owned empty observation scopes, and creates its audit event atomically. Applied
migration records and all earlier audit events remain.

Migration version 4 adds a unique audit-event ID and indexes event time plus operation and
target. An audit event stores only its opaque ID, operation, target database, UTC time,
non-secret actor/interface identifiers, plan digest, and affected-record count. It has no
field for application settings, credentials, evidence properties, packet content, or
payloads. Evidence export/import excludes audit nodes, and an audit-only target remains
empty for evidence-import purposes.

Every applied retention operation now creates the same minimal event in the exact profile
deletion transaction. Dry-runs create no audit record. The compatibility compute path and
`jaws-retention apply` supply fixed interface attribution at runtime.

## Consequences

- Agents can inspect and analyze evidence but cannot erase it through JAWS MCP.
- Operators must plan again whenever data or schema provenance changes.
- Evidence erasure does not destroy the record that it happened or the managed schema.
- The audit log proves operation metadata and counts, not the deleted evidence itself.
- Database-native backups remain necessary for unmanaged graph content that must survive;
  a portable evidence export is the managed-evidence backup path.
- Audit actor values identify the calling local interface, not an authenticated human
  identity. A future multi-user service must supply authenticated attribution explicitly.

## Verification

Offline contracts cover mutation-free planning, exact-confirmation rejection, database
binding, stale-plan rejection, payload-free event fields, durable fake audit ordering,
required CLI targets, removal of legacy unconfirmed Cypher, and absence of an MCP deletion
tool. A disposable Neo4j contract changes data after planning to prove rollback, then
applies a current plan and verifies that evidence is empty while migration records, the two
system scopes, and the new audit event survive.
