# Dependency and installation profiles

JAWS keeps numerical analysis lightweight and makes every external integration an
explicit installation choice. Python 3.12 is the supported baseline for the integrated
research-workbench rollout.

## Capability groups

The base installation contains NumPy, pandas, scikit-learn, Kneed, and Rich. It can
build endpoint profiles, execute the synthetic numeric benchmark, and use the pure
ranking functions without a database, provider SDK, capture stack, model stack,
plotting library, or MCP SDK.

| Extra | Packages | Responsibility |
| --- | --- | --- |
| `neo4j` | Neo4j driver | Graph evidence and current profile storage |
| `capture` | psutil, PyShark | Interface discovery, live capture, and PCAP import |
| `enrichment` | IPinfo | Organization and ASN enrichment |
| `openai-embeddings` | OpenAI SDK | Hosted endpoint embeddings |
| `local-embeddings` | Torch, sentence-transformers | Local CPU/GPU embeddings |
| `plotting` | Matplotlib, Plotille | File, interactive, and terminal plots |
| `mcp` | MCP SDK | MCP interface adapter |
| `agent-lab` | No third-party packages | Optional scripted OHEO laboratory behind standard-library protocols |
| `dev` | pytest, jsonschema, packaging, psutil, Ruff, mypy, PyYAML | Lightweight correctness, quality automation, and artifact-contract development |
| `all` | Every runtime extra | Backward-compatible complete runtime installation |

Extras are capabilities rather than transitive workflow bundles. For example, the
current OpenAI pipeline uses Neo4j, OpenAI, and plotting, so it selects all three extras.
This keeps a library or benchmark consumer from receiving integrations it does not use.

## Installation examples

Create the environment once:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
```

Install only the numeric core:

```bash
.venv/bin/python -m pip install \
  --constraint constraints/py312-direct.txt \
  --editable .
```

Install the current pipeline with hosted embeddings and MCP:

```bash
.venv/bin/python -m pip install \
  --constraint constraints/py312-direct.txt \
  --editable ".[neo4j,capture,enrichment,openai-embeddings,plotting,mcp]"
```

For local embeddings, replace `openai-embeddings` with `local-embeddings`. That is the
only profile that declares Torch or sentence-transformers. The backward-compatible
complete runtime remains:

```bash
.venv/bin/python -m pip install --requirement requirements.txt
```

The removable agent-laboratory spike adds no framework or model dependency:

```bash
.venv/bin/python -m pip install \
  --constraint constraints/py312-direct.txt \
  --editable ".[agent-lab]"
```

It uses the human-authored scripted model unless an operator implements the provider-neutral
laboratory protocol in a separately reviewed sandbox.

The lightweight development/correctness environment is:

```bash
.venv/bin/python -m pip install --requirement requirements-dev.txt
```

That profile also contains the pinned formatter/linter, type checker, and workflow
parser used by the [quality automation](quality.md).

## Version and lock policy

`pyproject.toml` is the source of truth for supported direct-dependency ranges and
capability ownership. [`constraints/py312-direct.txt`](../constraints/py312-direct.txt)
is the reviewed exact resolution for every direct dependency on Python 3.12. All
documented development, profile-verification, and release commands apply it.

The direct constraint set deliberately does not pretend that one transitive lock can
describe every supported platform: Torch CPU/GPU wheels, capture tooling, and compiled
numeric dependencies vary by operating system and accelerator. A benchmark or experiment
therefore records its complete installed-package inventory in its run environment. Later
container work may add platform-specific fully transitive locks without changing the
portable package contract.

Dependency updates follow one review unit:

1. Change the compatible range in `pyproject.toml` when support changes.
2. Update the exact direct pin in `constraints/py312-direct.txt`.
3. Run the clean installation profiles affected by the update.
4. Run correctness, CLI compatibility, and Benchmark 0 parity checks.
5. Record the complete resolved environment in any benchmark or experiment artifact.

Python 3.12 remains the only declared version until a later version passes every clean
installation profile, correctness/compatibility suite, and benchmark parity gate. Adding
a Python version requires an explicit support-policy update; it is not inferred from an
installer succeeding once.

## Verification

The profile checker creates a disposable virtual environment, installs one capability
under the reviewed constraints, runs `pip check`, and probes its imports:

```bash
# Lightweight release gates (the default)
python scripts/check_install_profiles.py

# Any selected profiles
python scripts/check_install_profiles.py --profile core --profile openai-embeddings --profile dev

# Complete matrix; downloads the large local-model stack
python scripts/check_install_profiles.py --all
```

Ordinary correctness tests also execute the complete synthetic numeric benchmark in an
isolated process that actively rejects imports of Neo4j, OpenAI, Torch,
sentence-transformers, capture, enrichment, plotting, and MCP packages. This protects the
runtime boundary even when a developer happens to have those packages installed globally.
