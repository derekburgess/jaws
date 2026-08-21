# Migrating from JAWS 2.0 to 3.0

JAWS 3.0 keeps the familiar capture, enrichment, compute, finder, and utility command names
as compatibility adapters. The major version reflects new MCP, database, experiment, and
artifact contracts. Migrate a copy first and retain the pre-migration evidence export.

## Installation and settings

Python 3.12 remains required. Choose capability extras instead of assuming the complete
runtime:

```console
python3.12 -m venv .venv
.venv/bin/python -m pip install --constraint constraints/py312-direct.txt \
  --editable ".[neo4j,capture,enrichment,openai-embeddings,plotting,mcp]"
```

The existing `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, provider-key, and
`JAWS_MCP_TIMEOUT` environment names are preserved. Settings define connections and
credentials; interface, duration, database, model, and session remain command arguments.
No secret should be copied into a specification, catalog, bundle, Compose file, or image.

## CLI behavior

The 2.0 entry points remain available. `jaws-compute --api numeric` adds a provider-free
path, and `--retain-profiles` delegates to the managed retention policy. New research work
should use:

- `jaws-research` for validation, run lifecycle, comparison, inspection, and portable
  experiment bundles;
- `jaws-schema` for schema status, dry-run, and migration;
- `jaws-evidence` for checksummed evidence export/import;
- `jaws-retention` and `jaws-admin` for explicit plan/apply administration;
- `jaws-benchmark` for governed Benchmark v1 profiles.

Legacy JSON envelopes remain frozen where compatibility tests require them. New service,
research, and MCP responses use versioned envelopes and stable error codes. Scripts should
check both `ok` and `schema_version`, not scrape Rich output.

## MCP clients

MCP v2 removes the old pipeline-orchestration surface. It does not shell out to legacy
commands and exposes no capture or destructive database operation. Update clients to the
catalog-scoped research tools in the generated [MCP reference](reference/mcp-v2.md).

Small reads and validation are synchronous. Long work follows:

```text
experiment_start → experiment_status → experiment_result
                                   ↘ experiment_cancel
```

Clients must retain the returned run ID, tolerate a pending state, obey pagination and
cost/concurrency limits, and handle stable error codes. Stdio and loopback Streamable HTTP
are supported; the removed SSE-era configuration is not.

## Database migration

JAWS 3.0 owns an ordered, checksummed migration ledger and currently manages schema version
7. Do not initialize schema through `jaws-utils`, delete old nodes, or point a qualification
run at the only copy of research evidence.

```console
# Against the 2.0 database, before changing it:
jaws-evidence export /secure/backups/jaws-2-evidence.json --database captures
jaws-evidence validate /secure/backups/jaws-2-evidence.json

# Prefer a copied database for the first migration:
jaws-schema status --database captures-copy
jaws-schema dry-run --database captures-copy
jaws-schema migrate --database captures-copy
jaws-schema status --database captures-copy
```

The migrations adopt legacy captures, retain legacy IDs and evidence, add collision-safe
capture identity/lifecycle, observation scopes, enrichment/profile provenance, tri-state
outlier compatibility, pooled-scope handling, and experiment/finding discovery indexes.
Legacy profiles that cannot be assigned a supported scope are quarantined rather than
silently reinterpreted. Applied migration checksums are immutable; unknown or altered
history fails closed.

## Evidence and experiment artifacts

Managed-evidence export/import is separate from experiment bundles. Evidence import
requires an empty, compatible target and applies atomically after checksum verification.
Experiment bundles are the canonical analytical record and can be inspected without
Neo4j. Neither bundle format redistributes source PCAPs by default.

2.0 output files without a versioned manifest remain legacy observations. Keep them for
history, but do not relabel them as 3.0 experiment bundles. Use the named `legacy_2_0`
ranker and Benchmark 0 when an analytical comparison with the old detector is required.

## Containers

Replace `harbor/` and `ocean/` automation with `compose.dev.yml`; those legacy build
directories were removed in 3.0. Build from the checked-out revision, provide the source
revision/build date, and pass credentials at runtime. CPU analysis is the default; GPU and
edge overlays are explicit. Only the edge sensor receives capture capabilities, and the
experimental agent laboratory has no network.

## Rollback

Schema migrations are additive where possible, but the supported rollback is evidence
recovery, not an unversioned reverse script: stop writers, preserve logs, restore the
pre-migration database copy or import the verified evidence bundle into a freshly managed
empty database, then verify counts and capture/profile identities before resuming work.
