# ADR-0007: Freeze Benchmark 0 before detector refactoring

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0001](0001-network-anomaly-ranking-research-workbench.md), [ADR-0004](0004-separate-experiment-specifications-and-runs.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#milestone-0--research-contract-and-benchmark-0) |

## Context

JAWS contains valuable but tightly coupled analytical behavior: historical
baselines, cadence exemptions, first-seen semantics, perspective translation,
host-outbound ranking, heuristic scores, DBSCAN labels, and explanations. Unit
tests protect several invariants, but the current quality harness does not retain
complete machine-readable rankings or a reproducible environment record.

Refactoring first would make it impossible to distinguish intentional method
improvements from accidental semantic drift. Requiring every scenario to pass,
on the other hand, would hide the current system's known benign false positives
and turn a baseline measurement into a retrospective claim of quality.

## Decision

Before changing detector, feature, score, threshold, explanation, or ranking
behavior, JAWS will create Benchmark 0 against code revision `0b68a8c`.
Documentation and collection tooling may be developed on the integration branch,
but the code under analytical test remains the named revision and its provenance
is explicit.

Benchmark 0 is an observational freeze, not a release gate asserting that the
detector is correct. It retains:

- the existing correctness and quality-test inventory;
- full rankings for every built-in synthetic scenario, not only pass/fail or the
  planted target's rank;
- known threat successes and benign false positives without weakening them into
  green assertions;
- explicit skips and reasons for unavailable optional real-PCAP evidence;
- endpoint and host-outbound ranking surfaces;
- text-only, numeric-only, and blended ablations where currently supported;
- scores, ranks, reasons, model/outlier labels, parameters, seeds, commands,
  stdout/stderr, exit status, and timing;
- environment, package, provider/model, dependency, container, and graph-schema
  inventories; and
- machine-readable manifests/results, a derived report, and checksums.

No detector refactoring begins until the Benchmark 0 completion gate in the
implementation plan passes. Subsequent decomposition preserves a named
`legacy_2_0` configuration and compares it with Benchmark 0 within declared
tolerances.

Benchmark 0 is evidence about past behavior, not a permanent optimization target.
After parity exists, intentional analytical changes are evaluated as experiments
and may improve or deliberately depart from it. The original artifacts and known
failures are never rewritten to make later results look better.

The machine-readable artifact schemas are the next Milestone 0 task and are not
selected by this ADR.

## Alternatives considered

### Refactor first and rely on current unit tests

This is faster initially, but the tests do not preserve complete ordering,
reasons, ablations, environment drift, graph contracts, or all ranking surfaces.

### Require Benchmark 0 to pass every quality scenario

This produces an attractive green report but would change the current baseline,
hide known false positives, and encourage benchmark manipulation before the
research platform exists.

### Capture only aggregate Recall@k

Aggregate metrics are compact, but they conceal benign observations above the
target, ranking movement, ties, explanation changes, and scenario-specific
regressions.

### Use the latest integration-branch code as the baseline

This would simplify execution, but documentation and harness work could silently
move the analytical target. Naming `0b68a8c` separates code under test from the
tooling that records it.

## Consequences

### Benefits

- Refactor parity becomes measurable rather than impressionistic.
- Valuable current invariants and known weaknesses are preserved together.
- Researchers can distinguish architecture changes from method changes.
- Simple and complex future rankers inherit a transparent reference point.

### Costs and limitations

- Baseline capture delays source decomposition.
- Reproducing an older dependency environment may be expensive or expose
  unpinned-version drift.
- Optional real evidence may remain absent because it cannot be redistributed.
- Benchmark 0 reflects a small scenario set and cannot establish general detector
  quality.

### Follow-on constraints

- Any unavoidable deviation while constructing the baseline is recorded, never
  silently patched into the code under test.
- Expected quality failures remain visible and distinct from harness/software
  failures.
- Reports are generated from retained machine-readable data.
- Changes capable of affecting analytical output require a Benchmark 0 parity or
  intentional-delta report.

## Benchmark impact

This ADR defines the purpose and minimum coverage of Benchmark 0. Its completion
gate is reproducibility and truthful retention, not all-green detector quality.
Benchmark v1 can add datasets, baseline rankers, metrics, policies, and held-out
tracks without replacing the frozen record.

## Migration

Run the analytical baseline from an isolated checkout/worktree at `0b68a8c`, using
the versioned collector and schemas developed on the integration branch. Commit
portable artifacts under `benchmarks/baseline-0/`, then update the implementation
plan with commands, environment evidence, limitations, and checksums. Detector
service extraction starts only afterward.

## Reversal conditions

If `0b68a8c` cannot be executed faithfully, record the failure and its dependency
cause. Selecting a substitute revision or modifying analytical code requires a
superseding ADR that quantifies the loss of comparability. The existing baseline
target and any partial artifacts must not be silently replaced.

## References

- [Milestone 0 scope and completion gate](../../IMPLEMENTATION_PLAN.md#milestone-0--research-contract-and-benchmark-0)
- [Compatibility ledger](../../IMPLEMENTATION_PLAN.md#compatibility-ledger)
