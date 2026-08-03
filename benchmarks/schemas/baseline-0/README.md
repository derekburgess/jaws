# Benchmark 0 schema set

These Draft 2020-12 JSON Schemas define version 1.0.0 of the Benchmark 0 measurement
contract.

| Schema | Validates |
| --- | --- |
| `manifest.schema.json` | Bundle identity, subject/collector separation, conventions, schemas, and artifacts |
| `dataset.schema.json` | Dataset catalog |
| `scenario.schema.json` | Scenario catalog |
| `run.schema.json` | Execution run and feature/dependency states |
| `environment.schema.json` | Environment provenance without secret values |
| `ranking.schema.json` | One complete endpoint or host-outbound ranking |
| `evaluation.schema.json` | Deterministic metrics and outcomes |
| `known-failures.schema.json` | Named detector-quality failures |

JSON Schema validates individual records. The collector's bundle validator additionally
enforces relationships that JSON Schema alone cannot express: scenario coverage,
contiguous ordering, target joins, evidence consistency, deterministic metrics, report
parity, secret scanning, artifact inventory, and checksums.

Changing a required field, status meaning, rank convention, or cross-record invariant
requires a new schema version. The frozen canonical Benchmark 0 must retain the exact
schemas used during collection.
