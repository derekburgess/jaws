# JAWS Benchmark 0

> This is a canonical Benchmark 0 result.

- Detector subject: `0b68a8c1ed615c96355989702126de623c78a714`
- Collector: `7cc27297a68512fcae825a1182ca06dd2ff0d892` (dirty: `false`)
- Completed scenarios: 8/11
- Recall@3: 1.000
- Mean reciprocal rank: 0.800
- Benign top-3 burden: 3

## Scenario results

| Scenario | Expect | Surface | Status | Target rank | Score | Quality |
| --- | --- | --- | --- | ---: | ---: | --- |
| payload_beacon | detect | endpoints | completed | 2/13 | 9.3024 | passed |
| jittered_beacon | detect | endpoints | completed | 2/13 | 7.4570 | passed |
| tcp_keepalive | reject | endpoints | completed | 1/13 | 12.8946 | failed_known |
| slow_exfil | detect | host_outbound | completed | 1/12 | 121.5759 | passed |
| burst_exfil | detect | host_outbound | completed | 1/12 | 171.6456 | passed |
| bulk_download | reject | host_outbound | completed | 2/12 | 3.4157 | failed_known |
| stable_heavy | reject | endpoints | completed | 3/13 | 3.2411 | failed_known |
| behavioral_change | detect | endpoints | completed | 1/13 | 62.4950 | passed |
| njrat_c2 | detect | endpoints | skipped | — | — | not_evaluated |
| masslogger_exfil | detect | host_outbound | skipped | — | — | not_evaluated |
| njrat_ipcheck | reject | endpoints | skipped | — | — | not_evaluated |

## Known failures reproduced

- `BF0-KF-001`: Bare TCP keepalive cadence crowds the endpoint ranking (tcp_keepalive)
- `BF0-KF-002`: Bulk download crowds the host-outbound ranking (bulk_download)
- `BF0-KF-003`: Historically stable heavy traffic remains highly ranked (stable_heavy)

## Unavailable evidence

- `njrat_c2`: dataset_unavailable: JAWS_PCAP_DIR does not contain 2026-01-29-njRAT-infection-with-MassLogger.pcap
- `masslogger_exfil`: dataset_unavailable: JAWS_PCAP_DIR does not contain 2026-01-29-njRAT-infection-with-MassLogger.pcap
- `njrat_ipcheck`: dataset_unavailable: JAWS_PCAP_DIR does not contain 2026-01-29-njRAT-infection-with-MassLogger.pcap

## Integrity

Every retained file except the checksum inventory itself is covered by `checksums.sha256`. The validator also checks schema versions, complete rank ordering, cross-record references, deterministic evaluation, generated report parity, and common credential patterns.
