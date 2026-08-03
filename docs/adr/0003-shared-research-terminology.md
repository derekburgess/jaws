# ADR-0003: Adopt shared research terminology

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0001](0001-network-anomaly-ranking-research-workbench.md), [ADR-0002](0002-analytical-axes-and-research-operations.md), [ADR-0004](0004-separate-experiment-specifications-and-runs.md) |

## Context

The current project uses terms such as capture, session, profile, anomaly,
outlier, result, baseline, and experiment in overlapping ways. That ambiguity is
manageable in one pipeline but unsafe once manifests, schemas, benchmark reports,
MCP contracts, and agent prompts must agree. In particular, an execution must not
be mistaken for an experiment definition, an outlier label must not be mistaken
for a malicious verdict, and an IP address must not be mistaken for a durable
device identity.

## Decision

JAWS adopts the following normative vocabulary. Schema/type names use the shown
PascalCase names where applicable; prose may use the lower-case form.

| Term | Meaning |
| --- | --- |
| Dataset (`DatasetManifest`) | A versioned declaration of one or more evidence sources, acquisition and licensing metadata, labels, splits, and checksums. A file alone is not a dataset. |
| Capture (`CaptureRecord`) | One bounded live-capture or import event, with source identity, time bounds, lifecycle status, host perspective, and tool provenance. |
| Observation window (`ObservationWindow`) | The exact capture IDs, temporal bounds, filters, timezone, and perspective included in one analysis scope. |
| Entity (`EntityDefinition`) | The versioned unit represented and ranked, such as endpoint-IP, host-destination, flow, service, or subnet. Entity identity is scoped by its definition and evidence context. |
| Representation (`RepresentationSpec`) | The declared features and transformations used to describe an entity, including missing-value, text-template, and embedding provenance where applicable. |
| Reference (`ReferenceSpec`) | The declared population and eligibility rules against which represented entities are compared. |
| Ranker (`RankerSpec`) | A versioned method and parameters that assign scores and a deterministic order to eligible entities. |
| Hypothesis (`HypothesisSpec`) | A falsifiable claim with a control, treatment, target scenarios, success metric, regression budget, and required evidence. |
| Experiment (`ExperimentSpec`) | The immutable, canonical declaration of a study. It says what is to be tested; it is not an execution or mutable folder of results. |
| Run (`ExperimentRun`) | One lifecycle-tracked execution attempt of an experiment specification in a recorded software and hardware environment. |
| Finding (`RankedFinding`) | One ranked entity record containing rank, scores, flags, contributions, explanation data, and evidence pointers. A finding is not a threat verdict. |
| Evidence pointer (`EvidencePointer`) | A stable locator joining a finding to its capture, window, entity, and supporting stored or checksummed evidence. |
| Metric | A named, versioned, deterministic measurement with declared direction, units, aggregation, and inputs. |
| Evaluation result (`EvaluationResult`) | The reward vector and scenario-level metrics produced by applying declared evaluators and labels to rankings. |
| Observation (`ObservationReport`) | A deterministic comparison of runs or conditions, including rank movement, metric deltas, regressions, and whether declared evidence supports or refutes a hypothesis. Human interpretation remains separate. |

The following distinctions are also normative:

- A **score** is a ranker's continuous or discrete output; a **rank** is the
  deterministic position after ordering and tie-breaking.
- An **outlier flag/label** is a method-specific result, such as DBSCAN noise; it
  is independent of whether a complete ranking exists.
- An **anomaly** is behavior that is unusual under a declared representation and
  reference. It is not synonymous with malicious activity.
- A **reward vector** retains component evaluation metrics. Any scalar objective
  derived from it must declare its weights and regression constraints.
- **Historical reference** means an entity's prior eligible observations.
  **Benchmark baseline** means a comparison method or frozen result. The word
  “baseline” must be qualified when both meanings are possible.
- **Session** remains a legacy CLI/database synonym for capture session during
  migration; new external contracts use `CaptureRecord` and
  `ObservationWindow` explicitly.

## Alternatives considered

### Continue defining terms locally in each interface

This minimizes central documentation, but schema fields, CLI help, MCP tools, and
benchmark reports would drift and become difficult to compare.

### Adopt generic machine-learning experiment terminology unchanged

Existing ML conventions help with tooling but often assume training, model
prediction, and scalar objective functions. They do not fully express packet
evidence, reference populations, evidence drill-down, or complete anomaly
rankings.

### Use “result” for every output

This is simple but loses the distinctions needed for provenance and lifecycle:
specification, execution, finding, metric, evaluation, and observation have
different identities and immutability rules.

## Consequences

### Benefits

- Documentation, Python types, schemas, CLI output, MCP tools, and reports share
  one conceptual model.
- Researchers can distinguish analytical evidence from interpretation and ground
  truth.
- Experiment and run identity can be validated without guessing from directory
  names or timestamps.
- Additional entity types do not weaken the meaning of existing endpoint records.

### Costs and limitations

- Legacy field names and command help will need compatibility aliases or migration
  notes.
- The vocabulary is more precise and therefore more verbose than the current
  pipeline's informal usage.
- Some terms, especially “meaningful anomaly,” still depend on declared labels or
  researcher judgment; terminology cannot remove epistemic uncertainty.

### Follow-on constraints

- External schemas must not use `experiment` and `run` interchangeably.
- A finding or outlier flag must never serialize as a malicious/benign verdict
  unless an independent labeled classifier contract is explicitly introduced.
- Entity records must include definition and perspective metadata.
- New public nouns require glossary review and, if they alter identity or research
  semantics, an ADR.

## Benchmark impact

Benchmark 0 may preserve legacy output names, but its manifest and report map them
to this vocabulary. Benchmark v1 schemas use the normative terms directly.
Metrics state direction and units, and reports distinguish expected quality
failures from software test failures.

## Migration

Milestone 1 introduces versioned domain types and compatibility mappings.
Subsequent CLI and MCP adapters translate legacy arguments and envelopes into the
new contracts until their documented deprecation gates are met. Existing Neo4j
labels are inventoried before any schema rename or migration.

## Reversal conditions

Revise a definition when implementation or research use demonstrates that it
combines objects with incompatible identity, lifecycle, or provenance. Changes to
accepted meanings require a superseding ADR and schema-version migration; adding
a non-conflicting glossary entry does not.

## References

- [Research object model](../../IMPLEMENTATION_PLAN.md#research-object-model)
- [Compatibility ledger](../../IMPLEMENTATION_PLAN.md#compatibility-ledger)
