# ADR-0016: Use checksummed evidence bundles and empty-target import

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-13 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0005](0005-evidence-store-and-portable-experiment-records.md), [ADR-0012](0012-ordered-neo4j-schema-migrations.md), [ADR-0015](0015-declared-retention-plan-before-apply.md), [repository contract](../storage/repositories.md) |

## Context

Managed schema versions 1–3 made evidence records explicit, but adoption of a destructive
migration or recovery from database loss still had no application-level export/import
procedure. A raw Neo4j dump is useful operationally but is tied to a database edition and
server layout, does not expose portable record checksums, and cannot be validated without a
database. Replaying ordinary repository methods is also insufficient: terminal capture
lifecycle rules prevent packet append, and legacy profiles deliberately lack identities
that current writers require. Legacy packet reads also intentionally retain observations
whose capture-catalog record is unavailable.

The procedure must preserve managed evidence rather than silently normalizing it. That
includes opaque capture and scope IDs, multiple legacy ownership edges, provider and
researcher provenance, null legacy profile identity fields, tri-state outliers, raw packet
payload text, and the exact ordered migration names and checksums.

## Decision

`jaws-evidence export` writes portable evidence-bundle format version 1 as canonical JSON.
The bundle contains seven deterministic sections: captures, observation scopes, packets,
entities with every ownership organization, provider enrichments, researcher annotations,
and endpoint profiles. Packet capture IDs remain intact even when the corresponding capture
metadata is unavailable. It records the current managed schema version and every migration
name/checksum, section record counts, one SHA-256 checksum per section, and a whole-content
checksum over schema provenance, counts, and section checksums.

Export writes a mode-restricted temporary file, flushes it, and atomically links the final
path. It never overwrites an existing path. `jaws-evidence validate` decodes all typed
records and verifies every checksum without importing configuration, loading the Neo4j
driver, or contacting a database.

Import is intentionally replacement-oriented rather than merge-oriented:

1. The target must already have a current managed schema whose ordered migration names and
   checksums exactly match the bundle.
2. `jaws-evidence import --dry-run` reports the reviewed counts, content checksum, schema,
   and whether the target is empty without writing.
3. Apply recalculates that plan, then repeats schema and emptiness checks inside one Neo4j
   write transaction. Schema-owned empty pooled/quarantine scopes do not make a fresh
   target nonempty; every other node—including unexpected or orphaned nodes—does.
4. The transaction recreates all managed records and derived packet port relationships in
   bounded statement batches inside the same transaction. Any statement failure rolls the
   complete import back. The service reads the restored snapshot and verifies its content
   checksum before reporting success.

The format is a portable export of the managed JAWS evidence contract, not an arbitrary
Neo4j database dump. Unexpected labels, properties, and relationships outside that contract
require a database-native backup when they matter.

## Alternatives considered

### Depend only on `neo4j-admin database dump`

Database-native dumps remain valuable for large operational backups, but they do not meet
offline portability and record-checksum requirements and may require a matching server
edition/version to inspect.

### Merge imports into a populated database

Merge semantics need explicit conflict policies for packet identity, capture lifecycle,
profile versions, annotations, and ownership. Guessing risks combining distinct evidence
histories. Empty-target restore has an auditable all-or-nothing meaning.

### Replay public capture, packet, and profile writers

Those writers correctly enforce live lifecycle and current-profile provenance. Bypassing
their rules in an archive adapter is necessary to restore terminal captures and
quarantined/unversioned records exactly; the adapter is constrained by matching schema and
empty-target checks instead.

### Omit raw packet payloads

That would make the bundle safer to distribute but would not restore the evidence store.
Experiment bundles continue to reference governed raw evidence rather than duplicate it;
evidence backup bundles are separately controlled and may contain sensitive payload text.

## Consequences

- Export/import preserves managed record counts, canonical IDs, provenance, schema history,
  multiple ownership edges, legacy null semantics, and sampled or complete checksums.
- A valid bundle is independently inspectable, but possession of it may expose packet
  payloads and enriched metadata. Operators must store and transmit it as sensitive
  evidence; secrets from application settings are never included.
- Import cannot update or merge a populated graph. Operators restore into a freshly
  migrated database and switch targets only after validation.
- The initial JSON representation is memory-resident. Streaming section encodings can be
  added as a new bundle format version without weakening version-1 validation.
- Future destructive or irreversible migrations have a concrete portable backup procedure
  to require before execution. This decision does not by itself authorize deletion.

## Verification

Offline contracts prove deterministic ordering, atomic no-overwrite publication, JSON
round-trip, tamper rejection, offline validation, schema mismatch rejection, mutation-free
dry-run, changed-target rejection, and exact checksum equality after import. A pinned
disposable Neo4j contract exports and restores terminal captures, packets and ports,
multiple ownership edges, enrichment, annotations, current profiles, quarantined legacy
profiles, and observation scopes, then compares the complete typed snapshot and checksum.
