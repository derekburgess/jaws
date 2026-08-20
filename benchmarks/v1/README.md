# JAWS Benchmark v1 catalog

Benchmark v1 is a versioned evidence catalog, not a traffic-data mirror. The repository contains deterministic aggregate fixtures and metadata for candidate external datasets; it contains no packet captures, payloads, credentials, or malware binaries.

## Included catalog records

| Manifest | Role | Local evidence | Source and terms |
| --- | --- | --- | --- |
| `jaws-synthetic-v1` | Development, validation, and held-out controlled scenarios | Deterministically generated bounded aggregate features | JAWS source, GPL-2.0-only |
| `ctu13-metadata` | Candidate validation capture | Not acquired | [CTU-13 primary page](https://www.stratosphereips.org/datasets-ctu13); [source license overview](https://www.stratosphereips.org/datasets-overview) |
| `cicids2017-metadata` | Candidate held-out capture | Not acquired | [CIC-IDS2017 primary page and license](https://www.unb.ca/cic/datasets/ids-2017.html) |
| `mawi-metadata` | Candidate held-out backbone capture | Not acquired | [MAWI archive and usage conditions](https://mawi.wide.ad.jp/mawi/) |
| `iscxtor2016-metadata` | Candidate validation counterexample | Not acquired | [ISCX Tor/non-Tor primary page and license](https://www.unb.ca/cic/datasets/tor.html) |

An external record's null acquisition date and checksum mean exactly “not acquired.” A source review date is not represented as an acquisition. The synthetic manifest's `sha256:identity` marker is resolved by the loader to the SHA-256 of the canonical versioned manifest or scenario document, so any governed fixture-input change changes cache and evidence identity. Before enabling an external record, an operator must separately review its current terms, acquire only the required traffic evidence and labels, compute a source-artifact checksum, document the actual acquisition date and bounded local location, and set the scenario to available. Executables and malware samples are never acquired.

## Running the governed profile

The smoke profile executes every registered ranker on one benign counterexample and one anomaly scenario, with two declared seeds:

```console
jaws-benchmark --tier smoke --output-dir benchmark-v1-smoke
```

The full profile uses the policy's five seeds, three windows, and all development and validation scenarios. It is intentionally scheduled/manual while thresholds stabilize:

```console
jaws-benchmark --tier full --output-dir benchmark-v1-full
```

Held-out labels require the explicit `--include-held-out` switch and the governed procedure in `docs/benchmark-governance.md`. JSON, Markdown, and HTML are renderings of the same retained `BenchmarkReport`; individual experiment bundles remain under the selected bundle directory.
