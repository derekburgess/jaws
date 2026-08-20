# Benchmark artifacts

JAWS benchmark bundles are versioned laboratory records. They preserve the evidence,
configuration, complete rankings, deterministic evaluation, environment, logs, and
integrity information needed to compare detector behavior across revisions.

`v1/` is the current governed ranker-research catalog and reference report. It uses the
Milestone 5 experiment-bundle format per scenario/ranker cell, while this document's
bundle contract below describes the frozen pre-refactor Benchmark 0 artifact. See
[`v1/README.md`](v1/README.md) for the current runner, dataset policy, and source records.

The committed `baseline-0/` directory is the **canonical Benchmark 0 bundle**. It is
the observational freeze of detector revision
`0b68a8c1ed615c96355989702126de623c78a714`, collected by committed collector revision
`7cc27297a68512fcae825a1182ca06dd2ff0d892` from a clean working tree.
Its CLI and Neo4j compatibility inventories were collected separately by committed
collector revision `8679f23c4b536f61961000b8f60406fd9ac3f5c2`; they retain their own
collector provenance without redefining the original ranking run.

The committed `examples/baseline-0/` directory is a **noncanonical contract fixture**.
It proves that the format can represent every current synthetic scenario, both ranking
surfaces, known detector-quality failures, and unavailable PCAP data. It must not be
cited as the frozen Benchmark 0 run.

## Bundle contract

The version 1.0.0 contract contains:

| File | Responsibility |
| --- | --- |
| `manifest.json` | Benchmark identity, detector subject, collector identity, commands, conventions, schema catalog, and artifact inventory |
| `datasets.json` | Dataset source, licensing, redistribution, availability, generator or file identity, checksums, and label provenance |
| `scenarios.json` | Exact scenario, target, surface, seeds, packet-row digest, observation window, history, and success rule |
| `run.json` | Lifecycle, invocation, analytical modes, dependency states, and per-scenario execution/quality status |
| `environment.json` | Python, packages, platform, hardware, tools, services, models, containers, and redacted environment-variable presence |
| `rankings.jsonl` | One complete ordered ranking per scenario, including raw attributes, scores, verdicts, reasons, and evidence pointers |
| `evaluation.json` | Deterministic per-scenario results and aggregate metric vector |
| `known-failures.json` | Named, reviewable detector-quality weaknesses and their regression guards |
| `logs/` | Captured stdout and stderr |
| `report.md` | Human report rendered only from machine-readable records |
| `schemas/` | The exact JSON Schemas used to validate the bundle |
| `checksums.sha256` | SHA-256 coverage for every other retained file |

The canonical bundle additionally contains `compatibility/cli-contract.json`,
`compatibility/neo4j-schema.json`, and their deterministic README. These are
source-derived compatibility reports declared by the manifest and covered by the root
checksum inventory; they do not extend or reinterpret the version 1.0.0 ranking schema.

The canonical schemas live in `schemas/baseline-0/`. A copy is embedded in each
bundle so an artifact remains interpretable without relying on a moving branch.
The development-only `jsonschema` dependency validates this external artifact
contract; it does not decide Milestone 1's typed domain-model library.

## Status semantics

Software execution and detector quality are separate:

| Execution status | Quality outcome | Meaning |
| --- | --- | --- |
| `completed` | `passed` | The detector ran and met the scenario rule |
| `completed` | `failed_known` | The detector ran and reproduced a named quality weakness |
| `completed` | `failed_unexpected` | The detector ran but departed from the expected baseline |
| `skipped` | `not_evaluated` | Required evidence or a dependency was unavailable |
| `failed` | `not_evaluated` | The harness or detector execution failed |

A known failure is not converted into a pass. Reproducing it proves baseline fidelity.

Ranks are one-based. `emitted_position` is the detector's zero-based output position.
Scores retain detector emission order; this contract does not introduce a new tie-break.
Unavailable values are explicit nulls or status values and are never inferred to mean
zero, false, success, or availability.

Evidence/source digests use UTF-8 JSON with keys sorted lexicographically, compact
separators, preserved array order, finite JSON numbers, and no ASCII coercion. Human
indentation is never part of a packet-row or source-tree identity.

## Validate the committed bundles

From the repository root:

```bash
PYTHONPATH=tests .venv/bin/python -m harness.benchmark_contract \
  validate benchmarks/baseline-0

PYTHONPATH=tests .venv/bin/python -m harness.benchmark_contract \
  validate benchmarks/examples/baseline-0

PYTHONPATH=tests .venv/bin/python -m harness.compatibility_contract \
  validate benchmarks/baseline-0/compatibility
```

The validator checks:

- Every JSON Schema and document
- Full scenario coverage and unique cross-record identities
- Contiguous ranks, emitted positions, score order, and target joins
- Evidence digests and finding-level evidence pointers
- Agreement among run, ranking, and evaluation statuses
- Deterministic recalculation of the evaluation vector
- Exact regeneration of `report.md`
- Manifest/artifact completeness
- Common credential patterns
- Every retained checksum

## Regenerate a noncanonical example

Collection refuses to overwrite a non-empty directory. Generate into a new temporary
path, inspect it, and replace the committed fixture only as an intentional documentation
change:

```bash
PYTHONPATH=tests .venv/bin/python -m harness.benchmark_contract \
  collect-example \
  --output /tmp/jaws-baseline-0-example \
  --subject-revision 0b68a8c \
  --collector-revision worktree
```

The collector verifies that `jaws/jaws_compute.py` and `jaws/jaws_finder.py` still
match the declared detector subject before it runs. The manifest records the detector
revision separately from the collector revision and source digest.

## Reproduce canonical Benchmark 0

Canonical collection requires a clean working tree, a committed collector revision,
and the exact detector subject. It refuses to overwrite an existing bundle. Reproduce
the retained run from the collector commit in a separate worktree:

```bash
git worktree add /tmp/jaws-benchmark-0-reproduction \
  7cc27297a68512fcae825a1182ca06dd2ff0d892
cd /tmp/jaws-benchmark-0-reproduction
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --editable ".[dev]"
PYTHONPATH=tests .venv/bin/python -m harness.benchmark_contract \
  collect-baseline \
  --subject-revision 0b68a8c \
  --collector-revision 7cc27297a68512fcae825a1182ca06dd2ff0d892
```

Runtime timestamps and durations may differ. Dataset manifests, scenario definitions,
complete ranking payloads, scores, reasons, evidence digests, quality outcomes, and
known-failure identities must remain equal. The committed environment record contains
the exact package versions used for the retained run.

## Reproduce the compatibility inventories

Compatibility collection must start from its clean collector revision, where the
canonical ranking bundle exists but the compatibility directory does not. It uses
deterministic fakes for external CLI boundaries and reads Cypher from the exact subject
revision, so it requires no credentials, live capture, Neo4j instance, model, or PCAP.

```bash
git worktree add /tmp/jaws-benchmark-0-compatibility \
  8679f23c4b536f61961000b8f60406fd9ac3f5c2
cd /tmp/jaws-benchmark-0-compatibility
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --editable ".[dev]"
PYTHONPATH=tests .venv/bin/python -m harness.compatibility_contract \
  collect \
  --bundle benchmarks/baseline-0 \
  --subject-revision 0b68a8c \
  --collector-revision 8679f23c4b536f61961000b8f60406fd9ac3f5c2
```

The CLI record retains seven exact agent-mode invocations, including both ranking
surfaces and three kinds of failure. The graph record is derived from 44 Cypher-bearing
source locations and inventories six labels, 40 node properties, five relationship
types, three uniqueness constraints, and five range indexes.

## Evidence and secret policy

- Never commit restricted PCAPs, extracted malware, credentials, or provider tokens.
- Record the expected PCAP filename, source, label provenance, and availability.
- A missing PCAP produces `skipped / not_evaluated`, never a pass.
- Environment-variable names and redacted presence may be retained; values may not.
- Synthetic packet rows are represented by canonical digests. The example retains full
  detector rankings, not duplicate raw row files.

The canonical run completed all eight controlled scenarios. All five detection
scenarios ranked within the top three; the three benign counterexamples remain named
quality failures. The three documented real-PCAP scenarios are explicit
`dataset_unavailable` skips because no redistributable fixture was present during the
capture.
