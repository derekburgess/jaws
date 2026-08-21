# Milestone 5 research system

Milestone 5 is complete at candidate revision `c1d9761` on 2026-08-20. It turns the
deterministic operations extracted through Milestone 4 into a reproducible research
control plane. Milestone 6 will add the broader benchmark datasets, ranker catalog, and
reward vector; it does not need to redesign experiment identity or artifact retention.

## Reproducibility contract

An `ExperimentSpec` is immutable and canonically serialized. Its SHA-256 content digest is
the experiment ID. Runtime IDs are unique per attempt and never change experiment identity.
The spec pins schema, evidence, label source, representation, reference, ranker, evaluator,
renderer, seed, and deterministic-library versions/settings. Catalog validation rejects
missing evidence, missing component versions, and incompatible ranker/representation pairs
before a run starts.

`ExperimentRun` records planned, queued, running, completed, failed, cancelled, and
superseded snapshots. Every transition appends a timestamped sequence entry and is written
with optimistic state checking. The local run journal publishes each snapshot atomically,
so status and failure detail do not depend on Neo4j. Failed runs retain their completed
stages and resumability; a retry gets a new run ID, points to its failed predecessor, and
supersedes that predecessor only after successful completion.

Canonical serialization redacts explicit `Secret` values and credential-shaped keys.
Unset `Secret` values remain `null`, preserving the distinction between never configured
and withheld. Provenance collects an allowlist of output-affecting facts and never dumps the
process environment.

## Canonical bundle layout

```text
<root>/experiments/<experiment-id>/runs/<run-id>/
├── manifest.json
├── experiment/spec.json
├── provenance/provenance.json        # completed runs
├── run/run.json
├── run/variant.json
└── artifacts/<variant>/
    ├── representation.json
    ├── reference.json
    ├── ranking.json
    └── evaluation.json

<root>/experiments/<experiment-id>/reports/<observation-id>/
├── manifest.json
├── experiment/spec.json
├── evaluations/*.json
└── observation/report.json
```

Writers stage a complete directory, calculate every checksum, verify the staged bundle,
and atomically publish it. Load verification reports missing, mismatched, and unexpected
files. Read-only verification and inspection use only the bundle. Raw PCAP/capture files
are rejected as embedded artifacts; export/import therefore moves analytical results and
evidence digests without redistributing captures.

Garbage collection is dry-run by default. It considers only failed, cancelled, or
superseded run bundles removable, and protects every run referenced by any retained
comparison/report. Completed runs and report bundles are always protected. Evidence lives
outside this store and is never a garbage-collection target.

When configured, `ExperimentService` publishes lifecycle summaries, experiment/run IDs,
bundle URIs, and manifest digests through `ExperimentIndexRepository`. The existing Neo4j
adapter implements that protocol; the external bundle remains canonical and offline
inspection never opens the graph.

## OHEO and CLI

The service layer implements the Orient → Hypothesize → Experiment → Observe loop:

- Orient lists evidence, labels, versioned components, earlier experiments, and benchmark
  summaries.
- Hypothesize validates a falsifiable control/treatment claim, primary metrics, explicit
  `maximize`/`minimize` objectives, and predeclared regression budgets. A budget limits
  movement opposite the declared objective rather than assuming every metric is maximized.
- Experiment executes bounded representation, reference, ranking, and evaluation stages,
  checking cancellation between stages and recording computed versus cached/reused
  artifacts.
- Observe deterministically calculates raw treatment-minus-control metric/cost deltas,
  direction-aware regressions, rank movement, and support/refutation status. Optional human
  interpretation is excluded from its deterministic digest. Follow-up hypotheses retain
  metric objectives and can retain the motivating observation ID.

The installed `jaws-research` entry point exposes `validate`, `run`, `status`, `cancel`,
`inspect`, `compare`, `verify`, `components`, `operation`, `export`, and `import`. Add
`--json` to any command for the stable automation result; the default summary is derived
from the same result object.

```bash
jaws-research validate examples/research/control-treatment.json \
  --catalog examples/research/catalog.json
jaws-research run examples/research/control-treatment.json \
  --catalog examples/research/catalog.json --root .jaws-research --json
jaws-research verify .jaws-research/bundles/experiments/<experiment>/runs/<run>
```

The declarative sample engine is deliberately bounded to JSON fixtures. It proves the
portable experiment path and provides an automation example; Milestone 6 registers real
baseline rankers and benchmark matrices through the same typed engine boundary.

## Validation evidence

The milestone tests cover schema validation, digest verification, unique run identity,
atomic transition conflicts, retry/supersession, cooperative cancellation, deterministic
replay, cache provenance, control/treatment observations, secret redaction, corruption
detection, offline inspection, raw-PCAP exclusion, portable export/import, garbage
collection, CLI JSON/human rendering, and repository-protocol discovery indexing.

The final gate uses:

```text
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/ruff format --check .
```

Live Neo4j execution is not required for bundle validation. The repository contract and
in-memory conformance adapter exercise indexing in the offline gate; a live Neo4j suite
remains environment-dependent and separately marked.
