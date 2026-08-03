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

The current analytical entity is an **endpoint profile**: one IP address, viewed during one capture session, with its inbound and outbound behavior aggregated into numeric features and a textual representation. The longer-term unit of reproducibility is the **experiment**, which will bind an observation window, entity definition, representation, reference population, ranker, parameters, software version, results, and evaluation artifacts into one immutable record.

### Orient → Hypothesize → Experiment → Observe

JAWS is being organized around a repeatable research loop:

1. **Orient** — inspect available captures, endpoint history, prior results, labels, and benchmark performance.
2. **Hypothesize** — state a falsifiable claim, its control, success metric, and acceptable regressions.
3. **Experiment** — run control and treatment configurations against declared data and retain their provenance.
4. **Observe** — compare rankings, false-positive movement, stability, explanations, and computational cost.

An example hypothesis might be:

> Adding per-destination upload/download asymmetry will improve exfiltration recall@3 without moving ordinary backup traffic into the top three results.

## How JAWS works today

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

MCP is an interface boundary, not the analytical core. Agents are optional research collaborators. They may propose hypotheses and configure bounded experiments, but scoring and rewards should remain deterministic, inspectable, and reproducible. An agent should not receive unrestricted capture privileges, destructive database access, or shell execution merely because it can call the research interface.

## Benchmark principles

Detector quality is separate from software correctness. JAWS evaluates both.

- **Unit invariants** protect statistical and historical-baseline behavior.
- **Controlled scenarios** test beaconing, exfiltration, scans, fan-out changes, and benign counterexamples.
- **Real PCAP scenarios** test whether improvements survive outside synthetic assumptions.
- **Simple baselines** such as bytes, first-seen status, upload ratio, and random ranking provide necessary floors.
- **Reward vectors** preserve tradeoffs instead of hiding them inside a single score.

Useful evaluation outputs include Recall@k, mean reciprocal rank, benign observations ranked above the target, rank stability, parameter sensitivity, explanation fidelity, runtime, memory use, and embedding cost. A more complex method should earn its place by outperforming simple sorts on held-out scenarios.

The existing recall harness is an early baseline rather than a finished benchmark. Run it with:

```bash
pytest -m recall -s
```

Set `JAWS_PCAP_DIR` to include the supported real-capture scenarios; otherwise those scenarios are skipped.

## Evidence, provenance, and non-goals

Research results should retain enough information to be reproduced and challenged: capture and session identifiers, observation scope, entity definition, feature and model configuration, reference population, ranking parameters, software version, ranked outputs, labels, metrics, and generated artifacts.

JAWS does not currently claim to:

- determine whether every anomaly is malicious;
- replace packet inspection or analyst judgment;
- provide a production IDS/IPS or turnkey SOC platform;
- establish detector quality from synthetic scenarios alone;
- treat IP addresses as perfect durable device identities;
- make agent-generated interpretations part of the ground truth.

An anomaly may be malicious, benign, novel, misconfigured, or simply worth understanding.

## Project status and direction

JAWS 2.0 is beta research software. The current endpoint model, historical baseline, explainable scoring, host-outbound view, MCP interface, and recall harness provide the behavioral baseline for the next refactor.

The planned direction is to:

1. Freeze the current detector as Benchmark 0.
2. Separate ingestion, enrichment, representation, comparison, ranking, explanation, storage, and evaluation behind typed Python APIs.
3. Introduce immutable experiment specifications, results, and provenance.
4. Compare rankers through a shared benchmark and reward-vector format.
5. Rebuild the Neo4j, analysis, capture, GPU, and MCP container boundaries for reproducibility and least privilege.
6. Make CLI and MCP thin adapters over the same core.
7. Explore an optional sandboxed research agent only after the experiment and evaluation foundations exist.

## Setup

### Requirements

- Python 3.12
- Neo4j
- Wireshark's `tshark`/`dumpcap` for live capture or PCAP import
- An OpenAI API key **or** sufficient local compute for sentence-transformer embeddings
- An IPinfo API key for organization and ASN enrichment
- Optional: an NVIDIA GPU and CUDA for faster local embeddings

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

```bash
git clone https://github.com/derekburgess/jaws.git
cd jaws
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install .
```

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

OpenAI embeddings are the CLI default. To run locally, pass `--api transformers`; public models in `jaws.config.PACKET_MODELS` do not require a Hugging Face key. The MCP server defaults to local transformers.

### 4. Start Neo4j

The repository currently includes an experimental Neo4j image in `harbor/`:

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

The current `harbor/` and `ocean/` images predate the planned container refactor: their base images are not fully pinned, and the compute image accepts credentials as build arguments. Treat them as development aids, not reproducible or hardened deployments. A local Python installation plus a separately managed Neo4j instance is the recommended research setup for now.

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

Do not drop the database between ordinary captures: earlier profile sets provide the endpoint history used by the baseline. Use `jaws-utils --drop captures` only when you intentionally want to erase the research dataset.

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
# Software correctness; Neo4j and recall tests are excluded by default.
pytest

# Detector-quality scenarios.
pytest -m recall -s

# Tests requiring a configured Neo4j instance.
pytest -m neo4j
```

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
