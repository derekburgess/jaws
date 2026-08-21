# Limitations and interpretation boundaries

JAWS allocates investigative attention. Its rankings, explanations, and agent narratives
are research observations—not autonomous malicious/benign verdicts.

## Anomaly is not threat

High-ranked behavior may be malicious, novel, misconfigured, rare but legitimate, or an
artifact of the chosen reference. Low rank does not prove safety. JAWS is not an IDS/IPS,
does not block traffic, and requires packet/evidence inspection plus analyst judgment.

## IP identity is contextual

An endpoint IP is an entity within declared captures and observation windows, not a durable
device or human identity. DHCP, NAT, shared services, proxies, IPv6 privacy addresses, and
routing changes can combine or split real actors. Capture perspective must be explicit;
host-relative upload/download semantics cannot be inferred reliably from an arbitrary
machine that later imports a PCAP.

## Dataset and label bias

The committed Benchmark v1 traffic is synthetic aggregate evidence. It isolates behaviors
and supports deterministic regression comparisons, but cannot establish performance on a
specific organization, network segment, threat family, or current adversary population.
External dataset entries are metadata-only until separately licensed, acquired, hashed,
safety-reviewed, and enabled. Labels may be incomplete or reflect their author's ontology.
Held-out labels are withheld from development by policy.

## Enrichment ambiguity

Organization, ASN, DNS, and IP classification data are time-sensitive provider
observations. They may be missing, cached, stale, rate-limited, reassigned, or inconsistent
across providers. Enrichment provenance and explicit unavailable/error states are retained;
provider text is not ground truth and must not silently change entity identity.

## Ranker and representation uncertainty

Rankings depend on entity definition, observation window, feature/template/model version,
reference population, normalization, ranker parameters, and seed. PCA/DBSCAN and isolation
methods may be sensitive to small samples or population composition. Embeddings can drift
with model/dependency/device changes. Simple baselines can outperform complex rankers.
Inspect per-scenario reward vectors, stability, benign burden, reasons, and evidence—not
only an aggregate score.

## Retention and scale

Neo4j is suitable for the current research evidence model but raw packets, profile history,
and embeddings can grow quickly. Profile retention does not imply packet deletion.
Administration is intentionally human-only and plan/apply guarded. Operators remain
responsible for storage sizing, backup protection, sensitive-evidence handling, and local
data-retention obligations.

## Agent boundary

The experimental OHEO laboratory can propose and interpret, but deterministic services
validate specifications, execute experiments, and calculate metrics. The reference agent
has no network, shell, capture, arbitrary file, Cypher, destructive administration, or
held-out-label authority. Prompt injection and citation checks reduce risk; they do not
make model output authoritative. A human must approve the exact experiment and evaluate
the resulting interpretation.

## Environment-specific gaps

GPU smoke requires matching NVIDIA hardware/runtime. Live capture requires OS-specific
permissions and is not part of the safe release smoke; the blocking edge test imports a
minimal payload-free PCAP. Real IPinfo/OpenAI/model and licensed-PCAP checks require
operator-provided resources and remain visible as unavailable when absent, never converted
into passing evidence.
