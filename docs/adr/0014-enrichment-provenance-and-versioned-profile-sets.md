# ADR-0014: Separate enrichment provenance, annotations, and versioned profile sets

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-12 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [ADR-0012](0012-ordered-neo4j-schema-migrations.md), [ADR-0013](0013-capture-identity-lifecycle-and-observation-scope.md), [repository contract](../storage/repositories.md) |

## Context

Legacy enrichment treats the presence of an `ORGANIZATION` relationship as its cache and
does not record provider, acquisition time, lookup outcome, or confidence. Researcher
labels could therefore be mistaken for provider facts. Legacy profile writes delete and
recreate rows one at a time, do not record representation/model revisions, and use
`CAPTURE_ID='all'` for a pooled aggregation that is not a chronological capture. An older
cleanup also deletes unstamped profiles whose provenance cannot be reconstructed.

The refactor needs explicit records without fabricating lost provenance or changing the
frozen CLI result envelopes.

## Decision

`EnrichmentRecord` stores one current provider observation per IP entity with explicit
status, acquisition time, provider ID/revision, optional confidence, and failure code.
Provider metadata is valid only for a successful result. `ResearcherAnnotation` is a
separate record and graph node, so researcher-authored labels never become provider
claims. Transient failures remain pending; successful, not-applicable, not-found, and
permanent outcomes are cached explicitly.

`EndpointProfile` uses the `ProfileIdentity` accepted in ADR-0013 and adds computed time,
legacy scope alias, representation values, embedding, explicit tri-state outlier status,
and profile provenance status. A repository replaces a complete scope atomically only
after every representation and embedding succeeds. It reads history by capture evidence
time, never by UUID, legacy alias, or recomputation time. The pooled `all` view has
`scope_pooled_all` and never participates in history.

Migration version 3 is additive. It maps pooled profiles to the pooled scope, quarantines
unstamped profiles in `scope_legacy_unstamped`, marks other old profiles
`legacy_unversioned`, and derives `OUTLIER_STATUS` as `outlier`, `inlier`, or `not_scored`
while preserving the legacy `OUTLIER` property. It never invents a profile key,
representation revision, model revision, or timestamp for old data.

The legacy compute CLI's mutable model name has no immutable revision input. The initial
compatibility bridge recorded `model_revision='runtime-unpinned'` and
`representation_version='legacy-v1'`. Milestone 3 now declares the exact endpoint text
template as `endpoint-description` version `1`, independently from model identity. The
writer still records `model_revision='runtime-unpinned'`; a later embedding-provider
checkpoint must replace that remaining limitation with declared provider/model revision
rather than treating the compatibility string as reproducible.

## Alternatives considered

### Continue using graph shape as the enrichment cache

This cannot distinguish provider success from researcher metadata, a permanent failure,
or a legacy `Unknown` placeholder, and it loses acquisition provenance.

### Delete or assign modern provenance to old profiles

Deletion discards evidence; assigning current representation/model identities makes a
false reproducibility claim. Quarantine preserves inspectability and states what is not
known.

### Store pooled `all` as an ordinary capture scope

The pooled view overlaps every capture and has no single evidence time. Letting it enter
history would contaminate both earlier and later baselines.

### Update profiles individually

This preserves the old implementation but exposes partial profile sets after model,
network, or database failures. Scope replacement in one transaction provides a stable
read boundary.

## Consequences

- Provider evidence and researcher assertions have independent provenance.
- Lookup failure and retry behavior is observable rather than inferred from missing
  relationships.
- A failed compute run leaves the prior complete profile scope intact.
- Legacy unstamped evidence remains available but cannot silently join current analyses.
- Compatibility fields remain dual-written while repository readers move to explicit
  scope and outlier status.
- Exact model revision remains unavailable on the legacy compute surface until Milestone
  3 introduces declared representation and embedding provider inputs.

## Migration and reversal

Version 3 adds one annotation constraint and two indexes, materializes pooled/quarantine
scopes, and stamps only legacy profile semantics. It does not automatically remove
`Unknown` ownership because public/non-public classification belongs in deterministic
Python; the enrichment repository performs that targeted, idempotent cleanup.

Before version-3 writers create records, rollback may restore scope IDs from the migration
marker and remove only version-3-derived properties/scopes/schema objects. Once new
enrichment, annotations, or versioned profiles exist, rollback requires export and restore.

## Verification

One shared behavior contract runs against deterministic in-memory repositories and pinned
Neo4j 5.26.28. Migration tests cover fresh and legacy graphs, pooled and unstamped scope
handling, tri-state verdicts, idempotence, and preservation of endpoint evidence.
