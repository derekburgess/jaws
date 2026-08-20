# JAWS

![JAWS cover](assets/cover.jpg)

JAWS is an open research workbench for investigating which representations, comparisons, and ranking methods surface behaviorally meaningful anomalies in network traffic—so security researchers can form hypotheses, run reproducible experiments, and trace ranked findings back to packet evidence.

JAWS is built for security researchers, blue teams, and technically capable hackers studying network behavior. It ranks observations for investigation; it does not claim that an anomaly is malicious or provide autonomous threat verdicts.

## The research question

> Which representations of network traffic, evaluated against which reference populations and ranked by which methods, most reliably bring meaningful anomalies to an investigator's attention?

The practical output of JAWS is therefore not a binary classification. It is an allocation of investigative attention: given an observation window and an investigative objective, what should a researcher inspect first, and why?

## Research model

JAWS separates four ideas that anomaly systems often blur together:

| Concept | Question | Examples |
| --- | --- | --- |
| Representation | What facts describe the entity? | Bytes, packets, peers, ports, protocols, timing, text embeddings |
| Reference | Compared with what? | Current peers, the endpoint's own prior sessions |
| Ranking | What makes an observation interesting? | Behavioral distance, novelty, cadence, fan-out, upload ratio, cluster isolation |
| Evaluation | Was the ranking useful? | Recall@k, reciprocal rank, benign burden, stability, runtime, cost |

The primary unit of reproducibility is the **experiment**. Each experiment binds an observation window, entity definition, representation, reference population, ranker, parameters, software version, results, and evaluation artifacts into one immutable record. An **endpoint profile**—one IP address viewed during one capture session, with its inbound and outbound behavior aggregated into numeric features and a textual representation—is one analytical entity an experiment can rank.

### Orient → Hypothesize → Experiment → Observe

JAWS follows a repeatable research loop:

1. **Orient** — inspect available captures, endpoint history, prior results, labels, and benchmark performance.
2. **Hypothesize** — state a falsifiable claim, its control, success metric, and acceptable regressions.
3. **Experiment** — run control and treatment configurations against declared data and retain their provenance.
4. **Observe** — compare rankings, false-positive movement, stability, explanations, and computational cost.

An example hypothesis might be:

> Adding per-destination upload/download asymmetry will improve exfiltration recall@3 without moving ordinary backup traffic into the top three results.

## How JAWS works

The current pipeline exposes five core operations through both command-line tools and an MCP server:

1. **Capture or import** packets into Neo4j as a timestamped capture session.
2. **Enrich** observed IP addresses with organization and ASN information.
3. **Profile** each endpoint's behavior and create an OpenAI or local sentence-transformer embedding.
4. **Rank** endpoint anomalies using behavioral scores and PCA/DBSCAN clustering.
5. **Inspect** a ranked endpoint by tracing it back to peers, session history, and raw packet samples.

Two complementary views are produced:

- **Endpoint ranking** compares remote endpoints with their peers or, when history exists, with their own earlier sessions.
- **Host-outbound ranking** examines the capture host's destinations directly for upload, exfiltration, and beacon-like patterns.

Captures accumulate rather than overwrite one another. After an endpoint appears in multiple profiled sessions, JAWS can compare its current behavior with its own history. This suppresses hosts that are consistently unusual and emphasizes meaningful change. Cadence features remain peer-relative because a persistent beacon could otherwise normalize itself.

Every ranked result includes human-readable reasons in the original units and identifies its reference frame (`own history` or `peer endpoints`). The `inspect_endpoint` MCP tool connects a finding back to its supporting evidence.

## Interfaces

JAWS can be used in two ways:

- The **CLI** is the direct interface for researchers, scripts, and benchmarks.
- The **MCP server** exposes the same pipeline to any compatible research client or agent.

MCP is an interface boundary, not the analytical core. Agents are optional research collaborators: they orient to prior results, propose falsifiable hypotheses, configure bounded experiments, and interpret deterministic observations. Scoring and rewards remain deterministic, inspectable, and reproducible. Agents operate in a separate sandbox and do not receive unrestricted capture privileges, destructive database access, or shell execution merely because they can call the research interface.

## Benchmark principles

Detector quality is separate from software correctness. JAWS evaluates both through a shared benchmark model:

- **Unit invariants** protect statistical and historical-baseline behavior.
- **Controlled scenarios** test beaconing, exfiltration, scans, fan-out changes, and benign counterexamples.
- **Real PCAP scenarios** test whether improvements survive outside synthetic assumptions.
- **Simple baselines** such as bytes, first-seen status, upload ratio, and random ranking provide necessary floors.
- **Reward vectors** preserve tradeoffs instead of hiding them inside a single score.

Useful evaluation outputs include Recall@k, mean reciprocal rank, benign observations ranked above the target, rank stability, parameter sensitivity, explanation fidelity, runtime, memory use, and embedding cost. A more complex method should earn its place by outperforming simple sorts on held-out scenarios.

The recall harness provides the command-line benchmark entry point:

```bash
pytest -m recall -s
```

Set `JAWS_PCAP_DIR` to include the supported real-capture scenarios; otherwise those scenarios are skipped.

The versioned [benchmark artifact contract](benchmarks/README.md) defines how JAWS
retains complete rankings, evidence identity, execution and quality outcomes,
environment provenance, known failures, generated reports, and checksums. The committed
[`benchmarks/baseline-0/`](benchmarks/baseline-0/) bundle is the canonical observational
freeze of the pre-refactor detector. The separate `benchmarks/examples/baseline-0/`
bundle remains a noncanonical contract-validation fixture.

## Evidence, provenance, and non-goals

Research results retain enough information to be reproduced and challenged: capture and session identifiers, observation scope, entity definition, feature and model configuration, reference population, ranking parameters, software version, ranked outputs, labels, metrics, and generated artifacts.

JAWS does not currently claim to:

- determine whether every anomaly is malicious;
- replace packet inspection or analyst judgment;
- provide a production IDS/IPS or turnkey SOC platform;
- establish detector quality from synthetic scenarios alone;
- treat IP addresses as perfect durable device identities;
- make agent-generated interpretations part of the ground truth.

An anomaly may be malicious, benign, novel, misconfigured, or simply worth understanding.

## Research architecture

JAWS 2.0 is beta research software organized around seven composable operations:

| Operation | Responsibility |
| --- | --- |
| Ingest | Capture live traffic or import PCAP and structured packet data |
| Enrich | Add organization, ASN, DNS, labels, and researcher annotations |
| Profile | Convert evidence into analytical entities and feature representations |
| Compare | Establish peer, historical, or researcher-defined reference populations |
| Rank | Apply one or more scoring and ranking strategies |
| Inspect | Trace ranked observations back to flows and packets |
| Evaluate | Compare rankings against labels, controls, baselines, and prior experiments |

Typed Python services define these operations. The CLI, MCP server, benchmark runner, and optional agent laboratory are adapters over the same deterministic core. Storage, representation, comparison, ranking, explanation, evaluation, and artifact management remain separable so researchers can add a feature family or ranker without rewriting the pipeline.

Neo4j stores packet evidence, relationships, capture history, endpoint profiles, and minimal
experiment/run/finding discovery indexes. Immutable external experiment specifications and
result artifacts remain the canonical analytical record; the graph stores only IDs,
lifecycle metadata, digests, artifact locations, and small searchable ranking projections.
Versioned database, sensor, analysis, GPU, and MCP containers separate packet-capture
privileges from analysis and agent execution.

The agent laboratory implements the **Orient → Hypothesize → Experiment → Observe** loop as an optional orchestration layer. Agent-generated hypotheses are proposals; deterministic code executes experiments and calculates rewards.

## Setup

### Requirements

- Python 3.12
- Neo4j only for the current graph-backed pipeline
- Wireshark's `tshark`/`dumpcap` only for live capture or PCAP import
- An OpenAI API key or sufficient local compute only when creating embeddings
- An IPinfo API key only for organization and ASN enrichment
- Optional: an NVIDIA GPU and CUDA for faster local embeddings

The numerical research core installs independently of every integration above. See the
[dependency and installation profiles](docs/dependencies.md) for the complete capability
matrix and version policy.

### 1. Install system dependencies

On Ubuntu:

```bash
sudo apt install wireshark tshark
sudo dpkg-reconfigure wireshark-common
sudo usermod -aG wireshark "$USER"
```

Log out and back in after changing group membership. Live capture permissions vary by operating system; PCAP import does not require a capture interface.

Install Neo4j locally, use Neo4j Desktop, or run the database container described below.

### 2. Install JAWS

For numeric-only research and the synthetic benchmark:

```bash
git clone https://github.com/derekburgess/jaws.git
cd jaws
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --constraint constraints/py312-direct.txt --editable .
```

For the current OpenAI-backed pipeline and MCP interface, select only those capabilities:

```bash
python -m pip install --constraint constraints/py312-direct.txt \
  --editable ".[neo4j,capture,enrichment,openai-embeddings,plotting,mcp]"
```

Replace `openai-embeddings` with `local-embeddings` for the local Torch/
sentence-transformer path. `python -m pip install --requirement requirements.txt`
retains the legacy complete-runtime installation.

For development, this single command creates the supported lightweight Python 3.12
environment and installs JAWS with its declared correctness-test dependencies:

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install --upgrade pip && .venv/bin/python -m pip install --requirement requirements-dev.txt
```

The commands below use `.venv/bin/python` directly, so activating the environment is
optional.

### 3. Configure the environment

JAWS expects these Neo4j settings:

```bash
export NEO4J_URI="bolt://localhost:7687"
export NEO4J_USERNAME="neo4j"
export NEO4J_PASSWORD="choose-a-password"
```

Configure the services you intend to use:

```bash
export IPINFO_API_KEY="..."       # enrichment
export OPENAI_API_KEY="..."       # OpenAI embeddings
export HUGGINGFACE_API_KEY="..."  # only for gated local models
export JAWS_FINDER_ENDPOINT="..." # optional plot output directory
```

Optional, unset by default:

```bash
export JAWS_MCP_TIMEOUT="900"  # server-side backstop; none by default
```

The environment carries connection details and credentials. Everything a run does — which
interface, how long, which database, which model, which session — stays on the command, so
a JAWS command means the same thing wherever it is pasted.

A credential is only required when the provider that needs it is actually invoked, so the
local-embeddings path runs with no API keys set at all. Settings can be serialized for
future run provenance with every secret redacted — see
[ADR-0011](docs/adr/0011-standard-library-settings-and-redacted-secrets.md).

OpenAI embeddings are the CLI default. To run locally, pass `--api transformers`; public models in `jaws.settings.DEFAULT_PACKET_MODELS` do not require a Hugging Face key. The MCP server defaults to local transformers.

### 4. Start Neo4j

The repository includes a Neo4j development image in `harbor/`:

```bash
cd harbor
docker build \
  --build-arg NEO4J_USERNAME="$NEO4J_USERNAME" \
  --build-arg NEO4J_PASSWORD="$NEO4J_PASSWORD" \
  --build-arg DEFAULT_DATABASE=captures \
  -t jaws-neodbms .

docker run --name captures \
  -p 7474:7474 \
  -p 7687:7687 \
  --detach jaws-neodbms
cd ..
```

The legacy `harbor/` and `ocean/` images use unpinned base images, and the compute image accepts credentials as build arguments. Treat these images as development aids rather than reproducible or hardened deployments. A local Python installation plus a separately managed Neo4j instance is the recommended setup when using this branch.

### 5. Run the pipeline

View the complete command guide:

```bash
jaws-guide
```

A typical local session is:

```bash
# List interfaces, then capture live traffic or import a PCAP.
jaws-capture --list
jaws-capture --interface eth0 --duration 60
# jaws-capture --file /path/to/capture.pcap
# For imported evidence, declare its original capture host when known.
# jaws-capture --file /path/to/capture.pcap --local-ip 192.0.2.10
# Optional PyShark filters are recorded as capture provenance.
# jaws-capture --interface eth0 --capture-filter "tcp port 443" --display-filter "tls"

# Enrich, profile, and rank the latest session.
jaws-ipinfo
jaws-compute --api openai --session latest
jaws-finder --session latest
```

For local embeddings:

```bash
jaws-utils --model jina-code
jaws-compute --api transformers --model jina-code --session latest
```

For a dependency-light numeric representation with no embedding provider:

```bash
jaws-compute --api numeric --session latest
```

Do not erase the database between ordinary captures: earlier profile sets provide the endpoint history used by the baseline. When a human operator intentionally needs a fresh research dataset, run `jaws-admin plan --database captures`, inspect the exact counts, then pass its full confirmation string to `jaws-admin erase --database captures --confirm 'ERASE captures …'`. Destructive administration has no default database and is not exposed through MCP.

Inspect retention before applying it explicitly:

```bash
jaws-retention dry-run --retain-profiles 20
jaws-retention apply --retain-profiles 20
```

The current finite policy applies only to computed profile sets. Raw packets and capture
metadata remain retained. `jaws-compute --retain-profiles` delegates to the same policy for
2.0 compatibility; use `0` to keep all profile sets.

Create a checksummed managed-evidence backup, validate it offline, and inspect a restore
into a separate freshly migrated database before applying it:

```bash
jaws-evidence export /secure/backups/jaws-evidence.json --database neo4j
jaws-evidence validate /secure/backups/jaws-evidence.json
jaws-schema migrate --database jaws-restored
jaws-evidence import /secure/backups/jaws-evidence.json \
  --database jaws-restored --dry-run
jaws-evidence import /secure/backups/jaws-evidence.json \
  --database jaws-restored
```

Before applying a future destructive or irreversible schema migration, export the live
pre-migration evidence and provide that exact verified bundle:

```bash
jaws-evidence export /secure/backups/pre-migration.json --database neo4j
jaws-schema dry-run --database neo4j
jaws-schema migrate --database neo4j \
  --backup /secure/backups/pre-migration.json
```

Import refuses populated or schema-incompatible targets. Evidence bundles may contain raw
packet payloads and enriched metadata; handle them as sensitive evidence and do not commit
them to the repository.

### 6. Run the MCP server

For a spawn-based MCP client:

```bash
jaws-mcp --stdio
```

For an SSE server:

```bash
jaws-mcp --host 0.0.0.0 --port 8765
```

The MCP tools follow the same sequence: `list_interfaces` → `capture_packets` → `document_organizations` → `compute_embeddings` → `anomaly_detection`. Use `list_captures`, `fetch_traffic`, and `inspect_endpoint` to orient and investigate without starting a new capture.

### 7. Run tests

```bash
# Formatting, linting, and the initial strict type boundary.
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy

# Collect every test item available in the current environment without running it.
.venv/bin/python -m pytest --collect-only -q -o addopts=""

# Software correctness; this is also the default pytest selection.
.venv/bin/python -m pytest -m "not neo4j and not recall"

# Neo4j integration. This tier skips explicitly when NEO4J_PASSWORD is absent.
.venv/bin/python -m pytest -m neo4j -rs

# Synthetic detector-quality scenarios only.
JAWS_RECALL_SOURCE=synthetic .venv/bin/python -m pytest -m recall -s

# Real-PCAP detector-quality scenarios only.
JAWS_RECALL_SOURCE=pcap JAWS_PCAP_DIR=/path/to/pcaps \
  .venv/bin/python -m pytest -m recall -s -rs
```

Correctness, integration, and detector quality are separate signals. The quality tiers
may expose known ranking failures; those outcomes are research observations rather
than reasons to weaken the scenarios. Restricted or licensed PCAPs are never committed.
When a requested real-PCAP fixture is absent, that tier reports an explicit skip.
The [quality-automation policy](docs/quality.md) documents CI jobs, the type-checking
ratchet, report-only benchmark artifacts, optional integrations, and cache provenance.

## History

### 2026 — MCP and historical behavior

JAWS 2.0 moved from bundled agents to an MCP-first interface that any compatible client can drive. Traffic analysis was reorganized around one behavioral profile per endpoint and capture session. Numeric features were blended with text embeddings, interpretable anomaly scores and per-feature reasons were added, and the host-outbound view made the capture host's own upload destinations explicit.

Capture sessions and endpoint profiles began accumulating in Neo4j, enabling comparison with each endpoint's own past. First-seen endpoints, historical references, baseline values, cadence exemptions, population-shift handling, endpoint inspection, and embedding ablation were added. The recall harness began separating detector quality from ordinary software tests.

### 2025 — Agent experiments

JAWS explored agent-driven workflows over its command-line tools. `smol.py` used smolagents for a manager/analyst handoff, while `jaws-agent` used Microsoft Semantic Kernel with a Gradio command center. Both experiments were later removed so JAWS could expose a model-agnostic MCP research interface rather than bundle a particular agent framework.

### Earlier direction

JAWS began as a Python shell pipeline for capturing network traffic, enriching it with OSINT, storing it in Neo4j, and using graph queries, embeddings, PCA, DBSCAN, plots, and reports to explore the shape and activity of networks.

## License

JAWS is licensed under GPL-2.0-only.
