# ADR-0015: Require a declared retention plan before apply

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-12 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0005](0005-evidence-store-and-portable-experiment-records.md), [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [ADR-0014](0014-enrichment-provenance-and-versioned-profile-sets.md), [repository contract](../storage/repositories.md) |

## Context

JAWS 2.0 automatically deletes old profile sets after every compute run, controlled by a
single `--retain-profiles` integer. Raw packets and capture metadata are retained without an
equivalent declaration. Future experiment indexes and artifact bundles will add two more
independently governed stores. A deletion embedded in compute cannot be inspected before it
runs, does not state what other evidence classes keep, and can act on data that changed
after the caller last observed it.

## Decision

Every retention policy declares one rule for raw packets, capture metadata, profile sets,
experiment indexes, and artifact bundles. `keep_all` is the safe default. A finite rule is
accepted only where its deletion semantics are implemented; currently that is
`keep_latest` for complete, nonquarantined profile sets. Unsupported finite rules fail
closed instead of implying that a resource was pruned.

Retention is a two-step deterministic service operation:

1. `plan`/`dry_run` reads repository summaries and returns the exact retained, deleted, and
   protected sets without writing.
2. `apply` accepts that plan, recalculates it, validates every exact scope summary inside
   the deletion transaction, and either deletes the complete planned set or raises a
   retention conflict without partial deletion.

Quarantined legacy profiles are always protected. Profile age is computation time because
the policy bounds generated representations, while endpoint history continues to order
evidence by capture time. `jaws-retention` is the explicit operator interface. The legacy
compute flag temporarily constructs and applies the same complete policy after a successful
profile replacement so the frozen 2.0 envelope remains compatible; no deletion logic stays
inside compute.

## Alternatives considered

### Keep repository `prune(retain)` as the public operation

This combines selection and deletion, cannot expose a trustworthy dry run, and leaves the
other resource classes undeclared.

### Implement finite deletion for every resource immediately

Capture/packet referential behavior, experiment references, and portable-bundle garbage
collection require export and run-index contracts that do not exist yet. Guessing now risks
irreversible evidence loss.

### Apply a previously generated list without revalidation

Profiles can be recomputed between planning and apply. Deleting by scope ID alone would
silently delete a replacement that was not present in the reviewed plan.

## Consequences

- Dry-run is guaranteed mutation-free and serializes the exact proposed deletion set.
- A stale plan fails atomically rather than deleting newly replaced profile data.
- Raw packets, capture metadata, experiment indexes, and artifact bundles remain explicitly
  `keep_all` until separate safe policies are implemented.
- The default compute behavior remains compatible during the CLI migration period, but its
  retention decision is now a typed service call shared with the explicit CLI.
- Audit records and export-before-delete enforcement remain required before Milestone 2
  administration is complete.

## Verification

One shared contract runs against the in-memory and pinned Neo4j implementations. It proves
that dry-run performs no mutation, computation-time selection is deterministic, a changed
scope invalidates the plan without deletion, apply deletes exactly the reviewed record
count, and `keep_all` produces no deletion candidates. Offline tests also verify complete
resource declarations, fail-closed unsupported rules, quarantine protection, CLI
serialization, packaging, and the service import boundary.
