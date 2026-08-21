# Release evidence

Each candidate directory is a checksummed, secret-free qualification record. It records
the exact code subject, independently versioned contracts, test/benchmark outcomes,
container digests, unavailable resources, accepted quality observations, and review-gated
publication actions.

Validate a candidate checked out beside the code:

```console
python scripts/release_qualify.py release/3.0.0-rc1
sha256sum --check release/3.0.0-rc1/checksums.sha256
```

The compressed Benchmark v1 asset contains the full JSON/Markdown/HTML report and all
2,340 verified experiment run bundles. It contains no PCAP, packet payload, provider
credential, model weight, external malware sample, or host-private absolute path.
