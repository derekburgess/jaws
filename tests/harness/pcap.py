"""Load real capture files as labeled scenarios.

Real adversary traffic is the check on the synthetic generators: those encode OUR model
of a beacon, so a detector tuned against them can score well while missing the thing
itself. Here the timing, packet sizes and session structure are the malware's.

The capture is used as a scenario SOURCE, not as a standalone capture. In the njRAT
sample the C2 is 94% of 2,621 packets across 8 endpoints — scored on its own it is
found by volume alone, which tests nothing. Extracting the labeled conversation and
planting it in a realistic background is what makes the rank meaningful.

Requires `tshark` on PATH. Captures are NOT checked into the repo (see tests/harness/
fixtures/README.md); point JAWS_PCAP_DIR at wherever you extracted them.
"""
import os
import subprocess
from dataclasses import dataclass

from .scenarios import HOST, CAPTURE, Scenario

FIELDS = ["ip.src", "ip.dst", "tcp.srcport", "udp.srcport", "tcp.dstport",
          "udp.dstport", "frame.len", "_ws.col.protocol", "frame.time_epoch"]


def pcap_dir():
    return os.environ.get("JAWS_PCAP_DIR", "")


def available(name):
    return bool(pcap_dir()) and os.path.isfile(os.path.join(pcap_dir(), name))


def read_pcap(path):
    """Packet rows in the shape jaws_compute.build_endpoint_profiles consumes."""
    out = subprocess.run(
        ["tshark", "-r", path, "-T", "fields", "-E", "separator=|",
         *sum([["-e", f] for f in FIELDS], [])],
        capture_output=True, text=True, check=True).stdout

    rows = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) != len(FIELDS):
            continue
        src, dst, tsp, usp, tdp, udp_, size, proto, ts = parts
        if not src or not dst or not ts:
            continue
        # tshark emits comma-joined values when a field repeats across layers.
        first = lambda v: v.split(",")[0] if v else ""
        sport, dport = first(tsp) or first(usp), first(tdp) or first(udp_)
        rows.append({
            "src_ip": src, "dst_ip": dst,
            "src_port": int(sport) if sport.isdigit() else 0,
            "dst_port": int(dport) if dport.isdigit() else 0,
            "size": int(first(size) or 0),
            "protocol": (proto or "DATA").split(",")[0],
            "ts_ms": float(ts) * 1000.0,
            "capture_id": CAPTURE,
        })
    return rows


def extract(path, keep_ips, local_ip, t0=0.0):
    """Pull only the labeled conversation, rebase its clock, remap the infected host.

    Timestamps are rebased to t0 so planted traffic overlaps the background window
    instead of sitting years away, which would otherwise dominate interval_mean.
    The infected host is remapped onto HOST so the host-frame features (host_outbound,
    the outbound-from-host glosses) line up with the background's local address.
    """
    rows = [r for r in read_pcap(path)
            if r["src_ip"] in keep_ips or r["dst_ip"] in keep_ips]
    if not rows:
        return []
    base = min(r["ts_ms"] for r in rows)
    out = []
    for r in rows:
        r = dict(r)
        r["ts_ms"] = t0 * 1000.0 + (r["ts_ms"] - base)
        if r["src_ip"] == local_ip:
            r["src_ip"] = HOST
        if r["dst_ip"] == local_ip:
            r["dst_ip"] = HOST
        out.append(r)
    return out


@dataclass(frozen=True)
class PcapSample:
    filename: str
    local_ip: str
    note: str


# Labels are hand-entered from each sample's published IOCs file, never inferred.
NJRAT = PcapSample(
    filename="2026-01-29-njRAT-infection-with-MassLogger.pcap",
    local_ip="10.1.29.101",
    note="MTA 2026-01-29; 2,621 pkts / 449s; IOCs name the C2 and SMTP exfil host",
)


def _generator(sample, ips):
    def gen(t0=0.0, scale=1.0, _s=sample, _ips=ips):
        return extract(os.path.join(pcap_dir(), _s.filename), set(_ips),
                       _s.local_ip, t0=t0)
    return gen


def pcap_scenarios():
    """Real-traffic counterparts to the synthetic detect scenarios."""
    if not available(NJRAT.filename):
        return []
    return [
        Scenario("njrat_c2", "104.248.130.195", "detect", "endpoints",
                 _generator(NJRAT, ["104.248.130.195"]),
                 note="real njRAT C2 on tcp/7492 (MTA 2026-01-29 IOCs)"),
        Scenario("masslogger_exfil", "78.110.166.82", "detect", "host_outbound",
                 _generator(NJRAT, ["78.110.166.82"]),
                 note="real MassLogger SMTP exfil to cphost14.qhoster.net:587"),
        # Malware-driven but low-volume IP-reputation lookups. They are part of the
        # infection, yet they look like ordinary API calls and should not crowd out
        # the C2 — a detector that ranks these above the beacon is not useful.
        Scenario("njrat_ipcheck", "208.95.112.1", "reject", "endpoints",
                 _generator(NJRAT, ["208.95.112.1"]),
                 note="ip-api.com lookup by the infected host - real but not the threat"),
    ]
