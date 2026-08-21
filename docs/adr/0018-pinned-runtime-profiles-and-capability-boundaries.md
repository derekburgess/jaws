# ADR-0018: Pinned runtime profiles and capability boundaries

## Status

Accepted — 2026-08-20

## Context

The legacy `harbor/` image used a floating Neo4j tag and embedded authentication from build arguments. The legacy `ocean/` image cloned a moving branch, accepted every provider credential at build time, combined capture and analysis privileges, installed an unreviewed complete dependency set, and idled with `tail -f /dev/null`. Those properties make an experiment impossible to tie to an exact source/runtime boundary and risk credentials entering image history.

Milestone 7 requires a CPU-default deployment plus explicit database, MCP, GPU, and edge/sensor boundaries. It must preserve optional dependency groups and keep raw capture capabilities away from analysis and research interfaces.

## Decision

JAWS images build only from the checked-out repository context. `containers/images.lock.json` records exact versioned OCI index digests. `containers/Dockerfile` is multi-stage and produces separate non-root analyzer, MCP, and sensor targets; only the sensor target installs `tshark`. `containers/Dockerfile.gpu` is an optional CUDA/local-model analyzer and has no capture packages or capabilities.

Credentials are runtime environment/secrets only. Builds accept OCI provenance labels—source revision, package version, and build date—but no connection strings, tokens, passwords, or model credentials. Compose drops all Linux capabilities and uses a read-only root filesystem for Python services. The edge sensor adds only `NET_RAW` and `NET_ADMIN`; it receives no Docker socket or Neo4j/provider credentials. Analyzer and MCP use a private internal network by default and have no raw-evidence write path.

`compose.dev.yml` owns the pinned database, one-shot schema migration, CPU analyzer, MCP service, health dependencies, and persistent volumes. GPU and edge are additive files. Model/provider egress is opt-in and separately documented rather than silently available to every service.

Each experiment already records strategy/model versions and allowlisted container digests. Runtime profiles pass `JAWS_CONTAINER_DIGEST` and, when applicable, `JAWS_MODEL_DIGEST`; model digest is now a first-class strategy-provenance field. Image/package inventory and safe-PCAP smoke outputs are retained as audit artifacts.

## Consequences

- CPU research and correctness do not install CUDA or Torch.
- GPU correctness/capability is tested separately and is unavailable on hosts without the NVIDIA runtime.
- A source checkout, build arguments, base lock, output image digest, and experiment bundle can be joined without placing secrets in an image.
- Operators must deliberately supply current image/model digests and runtime credentials; Compose cannot infer the registry digest of a locally built image.
- Changing a base digest or support platform requires review of the image lock and a new runtime smoke artifact.
