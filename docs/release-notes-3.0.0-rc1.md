# JAWS 3.0.0 release candidate 1

JAWS 3.0 is the integrated research-workbench rollout. It keeps the 2.0 command adapters
and its analytically frozen `legacy_2_0` control while moving new work onto typed services,
versioned evidence, reproducible experiments, governed benchmarks, and a bounded MCP v2
research interface.

## Highlights

- Deterministic ingest, enrichment, profiling, representation, reference, ranking,
  explanation, inspection, evaluation, and experiment services now sit behind CLI, MCP,
  benchmark, and optional-agent adapters.
- Ordered Neo4j migrations 1–7 adopt starting-revision evidence without rewriting legacy
  identities. Checksummed evidence export/import, retention planning, and exact-confirmation
  administration provide explicit recovery and mutation boundaries.
- Immutable experiment specifications and append-only runs retain code, environment,
  component, dataset, evidence, evaluation, and artifact provenance.
- Benchmark v1 evaluates twelve registered rankers across governed seeds/windows and keeps
  simple baselines, stability, burden, cost, and full ranking evidence visible.
- Pinned CPU, GPU, edge-sensor, MCP, and experimental agent profiles build the checked-out
  revision. Analysis processes are non-root/read-only/capability-free; capture privilege is
  isolated to the edge sensor.
- MCP v2 provides thirteen catalog-scoped tools, stable error contracts, bounded async run
  lifecycle, pagination, cancellation, and stdio/Streamable HTTP transports without shell,
  arbitrary Cypher, capture, or destructive administration.
- The optional OHEO agent laboratory ships as an experimental dependency-free extra in a
  no-network container. It proposes and summarizes; deterministic code approves, executes,
  scores, and verifies.

## Compatibility

The Python package and container label version is `3.0.0`. MCP/research API is `2.0.0`, the
managed Neo4j schema is migration version 7, and stable portable schemas remain independently
versioned. See the [2.0 migration guide](migration-2-to-3.md), generated
[CLI](reference/cli.md) and [MCP](reference/mcp-v2.md) references, and
[ADR-0021](adr/0021-major-release-and-experimental-agent-lab.md).

## Qualification and known limits

The candidate's machine-readable qualification record, compatibility matrix, benchmark
manifest, image digests, risks, and checksums live under `release/3.0.0-rc1/`. Full
Benchmark v1 run bundles are a release asset rather than Git source because they expand to
thousands of files; their archive checksum and every internal run-bundle checksum are
retained in the release record.

No external PCAP dataset, real provider credential, model weight, malware binary, or raw
capture is distributed. Synthetic benchmark results are controlled regression evidence,
not a field-detection claim. GPU, live-capture, real-provider/model, and licensed-PCAP
availability are recorded explicitly per environment. See [limitations](limitations.md).

This candidate is not a final publication. Merge to `main`, external release-asset upload,
and the final tag remain gated on review of the complete qualification record.
