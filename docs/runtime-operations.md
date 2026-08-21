# Runtime profiles and operations

## Build and start

All builds use the repository root as context. Record the exact source revision and build time in OCI labels:

```bash
export JAWS_SOURCE_REVISION="$(git rev-parse HEAD)"
export JAWS_BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export NEO4J_PASSWORD="choose-a-runtime-password"
docker compose -f compose.dev.yml up --build --detach --wait
```

The database must become healthy and the one-shot `migrate` job must complete before analyzer or MCP starts. CPU analyzer health is available only on loopback port 8080; MCP and Neo4j ports are also loopback-bound. The default `jaws-core` network is internal, so provider/model downloads are unavailable and fail explicitly rather than silently falling back.

Build the optional profiles by composing files:

```bash
docker compose -f compose.dev.yml -f compose.gpu.yml up --build gpu-analyzer
docker compose -f compose.dev.yml -f compose.edge.yml up --build sensor analyzer
```

GPU requires the NVIDIA Container Toolkit and one declared GPU device. The sensor requires an explicit `JAWS_CAPTURE_INTERFACE`; it alone receives `NET_RAW` and `NET_ADMIN`. Run bounded capture/import commands with `docker compose run --rm sensor ...`; the long-lived sensor command is only a health endpoint and never starts a capture by itself.

After building, record image identities and pass the 64-character digest (without `sha256:`) into experiment containers:

```bash
docker image inspect jaws-dev-analyzer --format '{{.Id}}'
export JAWS_ANALYZER_IMAGE_DIGEST="...64 lowercase hex characters..."
export JAWS_MODEL_DIGEST="...64 lowercase hex characters..."  # GPU/model runs only
```

`jaws-runtime inventory` emits the Python/platform/package inventory plus these declared identities. Registry base identities are locked in `containers/images.lock.json`.

## Safe smoke tests

The smoke runner builds the CPU analyzer and sensor, proves the analyzer has no CUDA dependency, generates a payload-free one-packet PCAP using TEST-NET addresses, and parses it with `tshark` without requesting live-capture capabilities:

```bash
python scripts/container_smoke.py --output-dir runtime-smoke
```

Add `--include-neo4j` to start the pinned database, migrate it, restart it, and re-run migration as a persistence/idempotence check. GPU loading remains a separate host-specific check:

```bash
docker compose -f compose.dev.yml -f compose.gpu.yml run --rm gpu-analyzer health --role gpu
```

Provider/model absence is expected in the internal default profile. Numeric benchmark and bundle verification must still work. An embedding or enrichment operation must return its typed unavailable/configuration result; it must not invent data or initialize an undeclared remote provider.

## Evidence and experiment bundles

The `artifacts` volume stores active and completed experiment bundles. Analyzer writes new bundles; MCP mounts them read-only. Completed bundles are checksum-verified before use. Export a bundle to operator-controlled storage before removing the volume:

```bash
docker compose -f compose.dev.yml run --rm --entrypoint jaws-research analyzer \
  verify-bundle /var/lib/jaws/artifacts/experiments/EXPERIMENT/runs/RUN
docker run --rm -v jaws-dev_artifacts:/source:ro -v "$PWD/exports:/target" \
  python:3.12.4-slim-bookworm@sha256:a3e58f9399353be051735f09be0316bfdeab571a5c6a24fd78b92df85bcb2d85 \
  sh -c 'cp -a /source/. /target/'
```

Restore only into an empty replacement volume, then verify every bundle before attaching it to a service. Reset benchmark outputs by replacing only the `artifacts` volume after export; never remove `neo4j-data` or `raw-evidence` as a benchmark cleanup shortcut.

## Database backup, restore, export, and import

Portable JAWS evidence export is the preferred cross-environment backup because it includes schema provenance and checksums:

```bash
docker compose -f compose.dev.yml run --rm --entrypoint jaws-evidence analyzer \
  export /var/lib/jaws/artifacts/evidence-backup.json --database captures
docker compose -f compose.dev.yml run --rm --entrypoint jaws-evidence analyzer \
  import /var/lib/jaws/artifacts/evidence-backup.json --database captures
```

Import requires an empty, compatible target. Verify the exported file before any destructive or irreversible migration. For a native Neo4j dump, stop Neo4j, mount `neo4j-data` plus an operator backup directory into the exact pinned Neo4j image, and use `neo4j-admin database dump`; restore into a new empty volume with `neo4j-admin database load`. Never overwrite the only database volume. Start the replacement, run `jaws-schema validate`, and retain both the portable evidence bundle and native dump until inspection succeeds.

## Model cache

`model-cache` is independent from artifacts and database data. It may be removed and rebuilt only when every model is recoverable by exact revision/digest. Before pruning, emit an inventory, record model revisions/digests used by retained runs, and confirm no offline experiment depends on the cached files. Analyzer/MCP do not receive model-download egress by default.

## Egress policy

| Capability | Default | Allowed destination | Credential boundary |
| --- | --- | --- | --- |
| IP enrichment | Denied | Explicit IPinfo endpoint only | Analyzer job receives `IPINFO_API_KEY` at runtime |
| Remote embeddings | Denied | Explicit OpenAI endpoint only | Analyzer job receives `OPENAI_API_KEY` at runtime |
| Model download | Denied | Reviewed model registry/revision | GPU/model-preload job; cache volume only |
| Agent execution | Denied | MCP internal endpoint; provider only after approval | Separate Milestone 9 sandbox and budget |

Create an explicit non-internal egress network and attach only the one-shot job that needs it. Do not attach Neo4j, MCP, sensor, or the default analyzer service. Host networking and Docker-socket mounts are prohibited.

## Recovery and retention

Named volumes have independent retention: `neo4j-data` contains the graph, `artifacts` contains experiment records, `raw-evidence` contains edge traffic evidence, and `model-cache` is reproducible cache. Back up/export each according to its sensitivity. Use `docker compose down` without `--volumes` for ordinary shutdown. A volume deletion is a separately reviewed destructive operation and must name the exact project volume after verified export.
