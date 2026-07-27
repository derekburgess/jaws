# Real capture fixtures

Capture files are **not** checked into this repo — they contain live malware C2 traffic,
they are large, and redistributing them is the sample author's call, not ours. Download
them yourself and point `JAWS_PCAP_DIR` at wherever you put them:

```bash
export JAWS_PCAP_DIR=~/pcaps
conda run -n jaws pytest -m recall -s
```

Real-capture scenarios are skipped when the files are absent, so the synthetic suite
still runs without them.

## Samples used

### `2026-01-29-njRAT-infection-with-MassLogger.pcap`

From <https://www.malware-traffic-analysis.net/2026/01/29/index.html>.
2,621 packets over 449s. Archive password follows the site's published scheme
(`infected_YYYYMMDD`, so `infected_20260129`).

Download the **pcap** and **IOCs** archives only — the third archive on that page holds
the malware binaries and is not needed here. Extract `*.pcap` exclusively.

Labels, hand-entered from the published IOCs file (never inferred from the traffic):

| Role | Address | Detail |
|---|---|---|
| Infected host | `10.1.29.101` | remapped onto the harness HOST on load |
| njRAT C2 | `104.248.130.195` | tcp/7492 |
| MassLogger exfil | `78.110.166.82` | cphost14.qhoster.net:587, encrypted SMTP |
| IP-reputation lookup | `208.95.112.1` | ip-api.com — infection artifact, not the threat |

Note the C2 is 94% of this capture across only 8 endpoints, so scoring the file
standalone finds it on volume alone and proves nothing. `harness/pcap.py` extracts the
labeled conversation and plants it in the synthetic background instead.
