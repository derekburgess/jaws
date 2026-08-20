# Milestone 3 CLI compatibility adapters

`jaws-capture`, `jaws-ipinfo`, and `jaws-compute` remain the operator-facing commands, but
their execution paths now wire outer dependencies into deterministic services:

| Command | Service-owned work | Adapter-owned work |
| --- | --- | --- |
| `jaws-capture` | capture lifecycle, bounded packet writes, cancellation/finalization | argument parsing, interface/PCAP selection, optional PyShark loading, Rich progress |
| `jaws-ipinfo` | classification, cache policy, retry/pacing, provider outcomes | argument parsing, credential-bound IPinfo adapter, Rich progress |
| `jaws-compute` | deterministic entity profiling, representation validation, atomic profile replacement | session selection, legacy DataFrame projection, provider construction, Rich progress |

All executable Cypher remains in `jaws.storage`; the architecture ratchet rejects query
logic anywhere in these command modules. Historical helper functions imported by tests or
downstream scripts remain compatibility projections over repositories/services, not an
alternative execution pipeline.

The existing flags remain accepted. `jaws-compute --api` additionally accepts `numeric`,
which performs endpoint profiling and atomic numeric representation storage without
initializing OpenAI, Torch, or sentence-transformers. Existing `openai` and `transformers`
defaults/model flags retain their prior meanings.

The commands still use the shared `Reporter`, so terminal use retains Rich activity and
human summaries while agent mode retains the frozen structured envelopes. Benchmark 0's
CLI compatibility collector exercises success and handled-failure paths byte-for-byte;
new service-focused CLI tests separately prove delegation and dependency-lazy numeric mode.
