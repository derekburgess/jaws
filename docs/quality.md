# Quality automation

JAWS separates software correctness, integration availability, and detector quality.
The automation preserves that distinction: syntax or contract failures block a change,
while benchmark metrics remain visible research evidence until an explicit policy turns a
particular threshold into a release gate.

## Local quality gate

Install the constrained development profile once:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --requirement requirements-dev.txt
```

Run the same blocking checks as the primary CI workflow:

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest -m "not neo4j and not recall"
```

Use `.venv/bin/python -m ruff check --fix .` followed by
`.venv/bin/python -m ruff format .` for intentional mechanical cleanup. Review the
resulting diff and run the full gate before committing.

## Formatting and lint policy

Ruff targets Python 3.12 and formats the complete tracked Python tree with a 100-character
line limit. The initial blocking lint set covers import ordering, syntax errors, undefined
or unused names, and the stable pycodestyle error families `E4`, `E7`, and `E9`.

This lint set is a floor. It may expand but may not shrink. A rule that produces legacy
findings should be enabled only after the existing findings are fixed or represented by
narrow, explained per-file exceptions. New blanket ignores are not an acceptable way to
make CI green.

## Static-type ratchet

Mypy begins in strict mode on the files that already form typed infrastructure:

- `jaws/domain/`
- `jaws/ports/`
- `jaws/settings.py`
- `jaws/optional_dependencies.py`
- `scripts/benchmark_smoke.py`
- `scripts/check_install_profiles.py`

The boundary may expand but may not shrink. New domain, settings, error, result-envelope,
and service-port modules introduced during Milestone 1 must enter the strict boundary in
the same change. Existing files remain in scope when they import legacy untyped modules;
targeted interface types or local casts should isolate that legacy surface. Weakening a
strict flag, removing a covered file, or adding a broad ignore requires an explicit plan
entry with rationale and a replacement ratchet.

## Continuous-integration signals

[`ci.yml`](../.github/workflows/ci.yml) runs on every push and pull request, and can also
be started manually. Its independent jobs are:

| Job | Blocking condition | Evidence |
| --- | --- | --- |
| `quality` | Ruff lint/format or declared mypy boundary fails | Inline annotations and job log |
| `correctness` | Offline correctness or compatibility contract fails | JUnit artifact |
| `benchmark-smoke` | Harness execution or report structure fails | JSON metrics, Markdown artifact, and step summary |

The correctness job checks out complete Git history because the compatibility contract
must resolve the frozen Benchmark 0 subject revision rather than silently testing only
the current branch tip.

The benchmark job deliberately does not run the assertion-based `pytest -m recall` tier.
[`scripts/benchmark_smoke.py`](../scripts/benchmark_smoke.py) executes the same eight
synthetic scenarios and records ranks, scores, reasons, Recall@3, and benign top-three
burden with `policy: report_only`. A metric change therefore remains visible without
being labeled good or bad automatically. An exception, empty ranking, invalid rank, or
unexpected scenario set is still a software failure.

[`integration.yml`](../.github/workflows/integration.yml) is scheduled weekly and can be
started manually. It does not run on every change:

- The Neo4j job starts the exact declared community image with per-run ephemeral
  authentication, waits for connectivity, and runs the opt-in Neo4j test marker.
- The capture-tooling job installs `tshark`, the constrained capture extra, and verifies
  the external executable/import boundary without starting a live capture.

Restricted PCAP quality remains a local/manual tier until a legally redistributable or
access-controlled dataset is provisioned. Missing PCAPs remain explicit skips in
Benchmark 0 rather than being converted into CI passes.

## Cache policy

CI caches only pip's download cache. Every setup step names
`constraints/py312-direct.txt`, `pyproject.toml`, and `requirements-dev.txt` as cache-key
inputs, so a direct pin, capability declaration, or development profile change produces a
new key.

No model weights are cached. A future model job may add a cache only after the key includes
the exact model identifier and immutable model revision in addition to the relevant
dependency resolution. Mutable aliases such as `latest`, a model name without a revision,
or a dependency-only key are insufficient for research provenance.
