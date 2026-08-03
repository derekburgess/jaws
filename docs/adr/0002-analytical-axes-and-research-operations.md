# ADR-0002: Separate four analytical axes and seven research operations

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0001](0001-network-anomaly-ranking-research-workbench.md), [ADR-0003](0003-shared-research-terminology.md) |

## Context

The current implementation combines feature construction, reference population
selection, PCA/DBSCAN, heuristic scoring, explanations, graph queries, plotting,
and CLI behavior in a small number of large modules. A configuration change can
therefore alter several analytical ideas at once, making it difficult to state
what a study actually tested or to compare a new method with a control.

The research model needs one vocabulary for experimental variables and another
for executable application operations. Those concepts overlap but are not the
same: for example, a reference is an analytical choice, while `compare` is the
operation that constructs or applies it.

## Decision

Every JAWS ranking study declares four independently versioned analytical axes:

| Axis | Question answered | Examples |
| --- | --- | --- |
| Representation | What facts describe the entity? | Bytes, packets, peers, ports, protocols, timing, text embeddings |
| Reference | Compared with what? | Current peers, the entity's own prior sessions, a hybrid or researcher-defined population |
| Ranking | What orders investigative attention? | Robust distance, novelty, cadence, fan-out, upload ratio, cluster isolation |
| Evaluation | Was the ranking useful for the declared study? | Recall@k, reciprocal rank, benign burden, stability, runtime, cost |

JAWS exposes seven composable research operations:

| Operation | Responsibility |
| --- | --- |
| Ingest | Capture live traffic or import PCAP or structured packet evidence |
| Enrich | Add organization, ASN, DNS, labels, and researcher annotations |
| Profile | Convert evidence into declared entities and representations |
| Compare | Build the declared peer, historical, hybrid, or custom reference |
| Rank | Produce continuous scores, deterministic ordering, flags, and contributions |
| Inspect | Trace findings to profiles, relationships, flows, and packet evidence |
| Evaluate | Compare rankings with labels, controls, baselines, and prior runs |

These boundaries apply to domain contracts and services, not merely to command
names. Each axis must be identifiable in an experiment specification. Each
operation must be callable through a typed application service so that CLI, MCP,
benchmarks, notebooks, and optional agents do not implement competing analytical
paths.

The normal data flow is ingest, enrich/profile, compare, rank/explain, inspect,
and evaluate, but orchestration may omit optional operations or revisit inspection
without recomputing a ranking. Inspection and rendering must never change a
score. Enrichment unavailability must be represented explicitly rather than
silently changing entity eligibility.

## Alternatives considered

### Preserve one end-to-end detector pipeline

This has fewer public abstractions and resembles the existing code, but it makes
ablation, component substitution, unit testing, and experiment identity
ambiguous.

### Use conventional ingest/train/predict/evaluate ML stages

This vocabulary is familiar, but JAWS includes non-trained rankers, historical
references, evidence inspection, and rule-based baselines. Treating every method
as a trained model would distort the research design.

### Organize only around endpoint profiles

Endpoint profiles are valuable, but a fixed endpoint-centric pipeline cannot
cleanly support host-destination, flow, service, or later entity definitions.

### Treat every operation as an analytical axis

This would blur study variables with application mechanics. Ingest and inspect,
for example, are operations with provenance requirements but are not alternative
ranking hypotheses in every experiment.

## Consequences

### Benefits

- A hypothesis can name exactly what changes between control and treatment.
- Representations, references, and rankers can be tested and registered
  independently.
- Simple sorting baselines and complex methods share one ranking contract.
- The same services support direct research, benchmarks, and external adapters.

### Costs and limitations

- Explicit intermediate records and validation add more types than the current
  pipeline exposes.
- Some current functions cross several boundaries and will require staged
  extraction rather than mechanical file moves.
- Cross-axis optimizations must still expose their inputs and effects through the
  declared contracts.

### Follow-on constraints

- A ranker consumes declared representations and references; it does not query
  Neo4j to invent either one.
- An evaluator consumes ranked findings and labels; it does not special-case a
  particular ranker.
- Explanations consume recorded contributions and perspective metadata rather
  than reverse-engineering opaque output.
- New operations or axes require an ADR explaining why the existing model cannot
  express the research need.

## Benchmark impact

Benchmark artifacts must identify all four axes and the relevant operation
versions. Control/treatment comparisons should change one declared variable at a
time when feasible. Baseline rankers must use the same entity, representation,
reference, tie-breaking, and evaluation contracts as more complex rankers.

Benchmark 0 records the current combined implementation as a legacy
configuration; it is not required to have already achieved these internal
boundaries.

## Migration

Milestones 1 through 6 introduce the typed records and extract services in
dependency order. Current CLI behavior remains an adapter and compatibility
surface until parity tests pass. `jaws_finder.py` is decomposed by responsibility,
with the existing combined behavior retained as the `legacy_2_0` ranker/configuration.

## Reversal conditions

Revisit the boundaries if repeated implementations show that an axis cannot be
varied or evaluated independently without corrupting the scientific meaning of
the experiment. Any merger or new axis requires a superseding ADR and parity
evidence demonstrating how prior experiment specifications remain interpretable.

## References

- [Research model in the README](../../README.md#research-model)
- [Target architecture in the implementation plan](../../IMPLEMENTATION_PLAN.md#target-architecture)
