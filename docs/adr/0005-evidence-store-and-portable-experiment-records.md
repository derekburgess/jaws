# ADR-0005: Use Neo4j for evidence and portable bundles for experiment records

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0004](0004-separate-experiment-specifications-and-runs.md), [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md) |

## Context

Neo4j is useful for packet relationships, capture history, endpoint profiles, and
evidence drill-down. It is less suitable as the only representation of an
experiment: graph state changes over time, database exports are heavy, result
ordering is easy to leave implicit, and a reviewer should not need a matching
database instance merely to inspect or verify a ranking claim.

Conversely, replacing the graph with files would discard relationship queries and
the existing evidence model. The research workbench needs both operational
evidence access and portable, immutable research records.

## Decision

JAWS uses two complementary persistence roles:

1. **Neo4j is the evidence and relationship store.** It holds captures, packet
   observations, profiles, entity relationships, enrichment, and indexes needed
   for investigation. It may index experiment/run identities, status, digests,
   and artifact locations for discovery.
2. **A portable experiment bundle is the canonical record of a study and its
   execution results.** It retains versioned specifications, run provenance,
   ordered findings, metrics, observations, generated artifacts, logs as allowed
   by policy, and content checksums.

“Canonical” means that the portable bundle is the authoritative, independently
verifiable record of what was specified and produced. Neo4j must not be the sole
copy of experiment results, and a graph index must be reconstructable from
bundles.

Raw PCAPs and other sensitive or restricted evidence are not duplicated into
every bundle. They are referenced through dataset/capture identity, acquisition
metadata, and checksums. A bundle must make missing or inaccessible evidence
explicit. Secret values are never recorded.

An `ArtifactStore` protocol isolates bundle semantics from storage location. The
exact JSON/JSONL/Parquet encodings and artifact-store configuration remain due in
Milestone 5. Milestone 2 implements only the minimal reconstructable graph index;
this ADR fixes the responsibility boundary without prematurely selecting bundle
encodings or artifact infrastructure.

## Alternatives considered

### Store evidence and complete experiment results only in Neo4j

This provides one query surface and transactional database, but weakens
portability, content verification, ordered-result preservation, and independent
review. Reproducing a claim would require reconstructing compatible graph state.

### Treat files as derived exports while Neo4j remains authoritative

Exports are useful, but if they are optional derivatives they can drift from the
database and cannot serve as stable research citations.

### Replace Neo4j with an artifact-only or relational design immediately

This could simplify deployment or structured experiment storage, but it would
force a high-risk evidence migration before Benchmark 0 and before graph access
patterns are measured.

### Copy all raw evidence into every experiment bundle

This maximizes local completeness but creates severe duplication, licensing,
malware-handling, privacy, and repository-size risks.

## Consequences

### Benefits

- Experiment claims can be archived, checksummed, compared, and reviewed without
  reproducing mutable graph state first.
- Neo4j remains available for relationship-rich evidence inspection.
- Artifact storage can move from local files to another backend without changing
  experiment semantics.
- Restricted datasets remain separately governed while results retain traceable
  evidence identity.

### Costs and limitations

- JAWS must keep graph indexes and canonical bundles consistent without creating
  two competing authorities.
- Garbage collection, retention, and backup policies must cover both stores.
- A portable bundle may be verifiable but not fully executable when its governed
  raw dataset is unavailable.
- Atomic finalization and checksum verification add lifecycle complexity.

### Follow-on constraints

- Experiment services write bundles atomically and publish graph indexes only
  after canonical artifacts are finalized.
- Database loss must not destroy completed experiment records; artifact loss must
  be detectable from graph metadata and checksums.
- Rendered reports and plots are derived from retained machine-readable results
  and cannot become the only copy of a finding.
- Raw-evidence retention and experiment-artifact retention are separate policies.

## Benchmark impact

Benchmark 0 and later benchmark releases are portable, machine-readable artifact
sets with checksums and exact ordered results. Optional PCAP absence is an
explicit skip, not a pass. Dataset manifests point to governed evidence by digest
without committing restricted packet captures.

Benchmark comparisons must read canonical result artifacts rather than depend on
whatever graph state happens to exist at report time.

## Migration

Milestone 0 defines the first baseline artifact schemas and records the current
graph schema. Milestone 2 versions Neo4j and adds the minimal experiment/run graph
index contract. Milestone 5 implements canonical experiment bundles, atomic
artifact publication, and index reconstruction from those bundles. The existing
graph remains in place until repository contracts, export/import, and rollback
tests pass.

## Reversal conditions

Revisit the split if measured workflows show that portable bundles cannot retain
required provenance or that maintaining the graph index creates unacceptable
consistency failures. A replacement must still provide independently verifiable,
immutable experiment records and evidence traceability before the canonical role
can move.

## References

- [Canonical experiment bundle](../../IMPLEMENTATION_PLAN.md#canonical-experiment-bundle)
- [Evidence, provenance, and non-goals](../../README.md#evidence-provenance-and-non-goals)
