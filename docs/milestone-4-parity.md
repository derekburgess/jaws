# Milestone 4 parity report

Milestone 4 is complete at candidate revision
`fbf055b9232b3d1f92450f8f227344d758a397bc`. It was compared with the canonical
Benchmark 0 detector subject `0b68a8c1ed615c96355989702126de623c78a714` on
2026-08-20.

## Declared tolerances

- Rank and entity order: exact.
- Continuous score: absolute delta at most `0.0001` after the existing four-decimal
  compatibility serialization.
- Reasons: exact ordered feature, value, unit, direction, robust-z, comparison frame,
  baseline depth, baseline, and host-relative text.
- Model verdict/label: exact.
- Scenario execution status: exact.

## Result

All 11 scenarios matched. Eight executed synthetic scenarios produced identical ranks,
scores, reasons, and verdicts; the three real-PCAP scenarios remained identically skipped
because their external samples were unavailable. The maximum observed score delta was
`0.0`.

| Scenario | Rank/order | Max score delta | Reasons | Verdict | Status |
| --- | --- | ---: | --- | --- | --- |
| `payload_beacon` | exact | 0.0 | exact | exact | exact |
| `jittered_beacon` | exact | 0.0 | exact | exact | exact |
| `tcp_keepalive` | exact | 0.0 | exact | exact | exact |
| `slow_exfil` | exact | 0.0 | exact | exact | exact |
| `burst_exfil` | exact | 0.0 | exact | exact | exact |
| `bulk_download` | exact | 0.0 | exact | exact | exact |
| `stable_heavy` | exact | 0.0 | exact | exact | exact |
| `behavioral_change` | exact | 0.0 | exact | exact | exact |
| `njrat_c2` | exact | 0.0 | exact | exact | exact skipped |
| `masslogger_exfil` | exact | 0.0 | exact | exact | exact skipped |
| `njrat_ipcheck` | exact | 0.0 | exact | exact | exact skipped |

Benchmark 0's three open benign-top-three findings were deliberately reproduced:
`BF0-KF-001` (`tcp_keepalive`), `BF0-KF-002` (`bulk_download`), and `BF0-KF-003`
(`stable_heavy`). Detector-quality changes belong in a later named ranker/version rather
than this compatibility refactor.

## Validation commands

```text
PYTHONPATH=. .venv/bin/python -m pytest -q
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_benchmark_contract.py
PYTHONPATH=. .venv/bin/python -m pytest -q -m recall tests/test_recall.py
.venv/bin/ruff check .
.venv/bin/mypy
```

The milestone-wide offline gate passed with 311 tests and 24 environment-dependent tests
deselected. The Benchmark 0 contract suite passed all 24 tests. Recall-at-3 remained 5/5;
the recall command also reproduced the three known benign failures listed above. The
comparison services and compatibility adapter have direct unit, architecture, CLI,
inspection, rendering, schema-snapshot, and Benchmark 0 coverage.
