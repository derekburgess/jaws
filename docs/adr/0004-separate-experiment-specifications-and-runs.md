# ADR-0004: Separate immutable experiment specifications from execution runs

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0003](0003-shared-research-terminology.md), [ADR-0005](0005-evidence-store-and-portable-experiment-records.md), [ADR-0007](0007-freeze-benchmark-zero-before-detector-refactoring.md) |

## Context

A reproducible study and one attempt to execute that study have different
identities. Conflating them makes repeated runs look like separate hypotheses,
encourages results to overwrite earlier results, and prevents researchers from
measuring nondeterminism, platform variance, or dependency drift.

JAWS also needs to compare the same analytical specification across code or
runtime environments without silently changing the question being asked.

## Decision

`ExperimentSpec` and `ExperimentRun` are separate, versioned records.

`ExperimentSpec` is an immutable, secret-free declaration of the study. Its
identity is derived from canonical semantic content. Semantically identical
specifications have the same experiment identity. It includes inputs that can
change the analytical question or expected output, such as:

- hypothesis, control, treatment, target scenarios, and regression budget;
- dataset and observation-window declarations;
- entity, representation, reference, ranker, and evaluator specifications;
- analytical parameters, model/provider revisions, and relevant random seeds; and
- declared outputs and success metrics.

`ExperimentRun` records one execution attempt of that specification. It has a
separate run identity and includes:

- the experiment identity and specification digest;
- lifecycle state, timestamps, cancellation/failure information, and supersession
  links;
- source-code revision and dirty-state declaration;
- operating system, hardware, packages, containers, provider availability, and
  deterministic-library settings;
- runtime measurements, logs, artifact locations, and checksums; and
- the exact results produced by that execution.

A rerun appends a new `ExperimentRun`; it never overwrites a prior run. A run may
state that it supersedes another operationally, but both records remain
verifiable. Human-readable names are labels, not identity.

The exact canonicalization library and capture/experiment/run ID formats remain
open decisions for Milestones 1 and 2. This ADR fixes the identity separation, not
those encodings.

## Alternatives considered

### One mutable experiment record containing the latest results

This is convenient for a UI, but it destroys execution history and makes a result
impossible to reproduce after environment or parameter updates.

### Treat every execution as a distinct experiment

This preserves files but prevents repeated-run analysis and overstates the number
of independent hypotheses tested.

### Identify experiments by user-provided names and timestamps

Names and timestamps are readable, but neither proves semantic equivalence and
both are collision- or edit-prone.

### Include the entire runtime environment in experiment identity

This maximizes specificity but turns platform or dependency comparisons into
different experiments, preventing JAWS from measuring environmental sensitivity
of the same study.

## Consequences

### Benefits

- Repeated runs can expose nondeterminism and platform drift explicitly.
- Control/treatment intent remains stable even when an execution fails or is
  rerun.
- Cache and deduplication decisions can use specification content rather than
  mutable labels.
- A researcher can compare the same question across code revisions while
  retaining exact run provenance.

### Costs and limitations

- The system must manage two IDs, two schemas, and lifecycle relationships.
- Canonicalization needs strict versioning; semantically irrelevant formatting
  must not change identity.
- Deciding whether a field affects study semantics or only execution provenance
  requires deliberate schema review.

### Follow-on constraints

- Specifications cannot contain secrets, machine-local output paths, or mutable
  “latest” references without resolution rules.
- Runs are append-only records with explicit partial, failed, cancelled, and
  completed states.
- An observation comparing runs cites both run IDs and the shared or differing
  experiment specifications.
- Schema changes that alter canonical meaning require versioned migration rules.

## Benchmark impact

Benchmark manifests declare an immutable configuration; each benchmark execution
records a separate environment and run result. Repeated deterministic runs should
produce identical analytical ordering, while runtime-only fields may differ.
Stochastic methods declare seeds and are evaluated across multiple runs rather
than being mislabeled as separate experiments.

Benchmark 0 predates the full domain implementation, so its manifest and results
act as a compatibility representation of this separation.

## Migration

Milestone 5 implements the schemas, lifecycle, runner, and artifact layout after
typed core services exist. Existing recall-harness invocations and historical
outputs can be imported as legacy run records only when their configuration and
provenance are known; otherwise they remain explicitly incomplete historical
artifacts.

## Reversal conditions

Revisit field placement if reproducibility studies show that a run-environment
field consistently changes analytical semantics and must be part of the
specification, or vice versa. The experiment/run separation itself may be reversed
only with a replacement model that preserves rerun history, semantic identity,
and prior artifact interpretation.

## References

- [Experiment identity and reruns](../../IMPLEMENTATION_PLAN.md#experiment-identity-and-reruns)
- [Milestone 5](../../IMPLEMENTATION_PLAN.md#milestone-5--experiment-run-provenance-and-artifact-system)
