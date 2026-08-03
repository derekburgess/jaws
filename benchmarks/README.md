# Benchmark artifacts

JAWS benchmark bundles are versioned laboratory records. They preserve the evidence,
configuration, complete rankings, deterministic evaluation, environment, logs, and
integrity information needed to compare detector behavior across revisions.

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

## Validate the committed fixture

From the repository root:

```bash
PYTHONPATH=tests .venv/bin/python -m harness.benchmark_contract \
  validate benchmarks/examples/baseline-0
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

## Evidence and secret policy

- Never commit restricted PCAPs, extracted malware, credentials, or provider tokens.
- Record the expected PCAP filename, source, label provenance, and availability.
- A missing PCAP produces `skipped / not_evaluated`, never a pass.
- Environment-variable names and redacted presence may be retained; values may not.
- Synthetic packet rows are represented by canonical digests. The example retains full
  detector rankings, not duplicate raw row files.

The next Milestone 0 task uses this contract to execute and freeze canonical Benchmark 0.
