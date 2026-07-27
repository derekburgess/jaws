"""Labeled traffic generators.

Each scenario plants ONE endpoint and declares what the detector should do with it.
The reject scenarios matter as much as the detect ones: a harness of attacks only
would reward a detector that flags everything, which is the exact failure mode JAWS
has spent two weeks fixing.
"""
from dataclasses import dataclass, field

import numpy as np

HOST = "10.0.1.3"
WINDOW = 600.0          # seconds of traffic per session
CAPTURE = "SYNTH"
# Real captures are planted into the same window; the njRAT sample runs 449s.


def _pkt(src, dst, sport, dport, size, ts, protocol="TCP"):
    return {"src_ip": src, "dst_ip": dst, "src_port": int(sport), "dst_port": int(dport),
            "size": int(size), "protocol": protocol, "ts_ms": float(ts) * 1000.0,
            "capture_id": CAPTURE}


# ------------------------------------------------------------------ background

def background_packets(t0=0.0, seed=7):
    """A plausible benign mix: web, CDN, DNS, a chatty LAN device.

    Deliberately varied — a uniform background makes any plant trivially findable
    and the recall number meaningless.
    """
    rng = np.random.default_rng(seed)
    rows = []
    peers = [(f"93.184.{i}.{10 + i}", 443, 40, 1400) for i in range(6)]
    peers += [(f"151.101.{i}.91", 443, 25, 900) for i in range(4)]
    for ip, port, n, mtu in peers:
        # Bursty, human-shaped: clustered request/response, irregular gaps.
        t = t0 + rng.uniform(0, 60)
        for _ in range(n):
            t += abs(rng.normal(6.0, 5.0)) + 0.05
            if t > t0 + WINDOW:
                break
            rows.append(_pkt(HOST, ip, 44000 + rng.integers(0, 900), port,
                             rng.integers(60, 400), t))
            rows.append(_pkt(ip, HOST, port, 44000 + rng.integers(0, 900),
                             rng.integers(200, mtu), t + abs(rng.normal(0.04, 0.02))))
    # Router DNS — small, frequent, irregular.
    for _ in range(60):
        t = t0 + rng.uniform(0, WINDOW)
        rows.append(_pkt(HOST, "192.168.1.1", 5300 + rng.integers(0, 400), 53,
                         rng.integers(60, 90), t, "UDP"))
        rows.append(_pkt("192.168.1.1", HOST, 53, 5300 + rng.integers(0, 400),
                         rng.integers(90, 300), t + 0.01, "UDP"))
    return rows


# ------------------------------------------------------------------- scenarios

def _beacon(ip, period, jitter, size, t0, seed):
    """Regular callbacks carrying a payload — the shape real C2 has."""
    rng = np.random.default_rng(seed)
    rows, t = [], t0 + 5.0
    while t < t0 + WINDOW:
        rows.append(_pkt(HOST, ip, 51000, 443, size + rng.integers(-40, 40), t))
        rows.append(_pkt(ip, HOST, 443, 51000, size // 2, t + 0.08))
        t += period * (1.0 + rng.uniform(-jitter, jitter))
    return rows


def payload_beacon(t0=0.0, scale=1.0, seed=11):
    return _beacon("198.51.100.7", 30.0, 0.001, int(820 * scale), t0, seed)


def jittered_beacon(t0=0.0, scale=1.0, seed=12):
    return _beacon("198.51.100.23", 30.0, 0.20, int(760 * scale), t0, seed)


def tcp_keepalive(t0=0.0, scale=1.0, seed=13):
    """Metronomic but EMPTY — bare ACKs. Structurally a beacon, benign in fact.

    This is the 35.190.46.17 case from the 2026-07-25 run. It is expected to fail
    until the bytes_per_packet floor lands; that is the point of having it here.
    """
    rows, t = [], t0 + 1.0
    while t < t0 + WINDOW:
        rows.append(_pkt(HOST, "35.190.46.17", 36316, 443, 54, t))
        rows.append(_pkt("35.190.46.17", HOST, 443, 36316, 60, t + 0.002))
        t += 1.024
    return rows


def slow_exfil(t0=0.0, scale=1.0, seed=14):
    """Sustained host upload — the defender-frame signal, not the endpoints one."""
    rng = np.random.default_rng(seed)
    rows, t = [], t0 + 20.0
    while t < t0 + WINDOW:
        rows.append(_pkt(HOST, "203.0.113.44", 52001, 443,
                         int(1400 * scale) + rng.integers(-80, 80), t))
        if rng.random() < 0.06:
            rows.append(_pkt("203.0.113.44", HOST, 443, 52001, 66, t + 0.05))
        t += abs(rng.normal(1.2, 0.4)) + 0.1
    return rows


def burst_exfil(t0=0.0, scale=1.0, seed=15):
    rng = np.random.default_rng(seed)
    rows, t = [], t0 + 300.0
    for _ in range(int(500 * scale)):
        rows.append(_pkt(HOST, "203.0.113.90", 52777, 8443,
                         1400 + rng.integers(-60, 60), t))
        t += abs(rng.normal(0.01, 0.004))
    rows.append(_pkt("203.0.113.90", HOST, 8443, 52777, 120, t + 0.2))
    return rows


def bulk_download(t0=0.0, scale=1.0, seed=16):
    """Large inbound transfer — high volume, but the host is RECEIVING. Not exfil."""
    rng = np.random.default_rng(seed)
    rows, t = [], t0 + 100.0
    for _ in range(int(600 * scale)):
        rows.append(_pkt("150.171.109.74", HOST, 443, 41000,
                         1400 + rng.integers(-60, 60), t))
        if rng.random() < 0.08:
            rows.append(_pkt(HOST, "150.171.109.74", 41000, 443, 66, t + 0.03))
        t += abs(rng.normal(0.012, 0.005))
    return rows


def stable_heavy(t0=0.0, scale=1.0, seed=17):
    """Consistently large every session — the Anthropic case. History should absorb it."""
    rng = np.random.default_rng(seed)
    rows, t = [], t0 + 10.0
    while t < t0 + WINDOW:
        rows.append(_pkt(HOST, "160.79.104.10", 53100, 443,
                         int(1200 * scale) + rng.integers(-200, 200), t))
        rows.append(_pkt("160.79.104.10", HOST, 443, 53100,
                         int(300 * scale) + rng.integers(-80, 80), t + 0.09))
        t += abs(rng.normal(0.9, 0.5)) + 0.05
    return rows


@dataclass
class Scenario:
    name: str
    planted_ip: str
    expect: str                      # "detect" | "reject"
    surface: str                     # "endpoints" | "host_outbound"
    generator: callable
    prior_scales: list = field(default_factory=list)   # history sessions, if any
    note: str = ""

    def packets(self, t0=0.0, scale=1.0):
        return self.generator(t0=t0, scale=scale)


SCENARIOS = [
    Scenario("payload_beacon", "198.51.100.7", "detect", "endpoints", payload_beacon,
             note="30s period, low jitter, ~820 B/pkt"),
    Scenario("jittered_beacon", "198.51.100.23", "detect", "endpoints", jittered_beacon,
             note="30s +/-20%, ~760 B/pkt"),
    Scenario("tcp_keepalive", "35.190.46.17", "reject", "endpoints", tcp_keepalive,
             note="1.024s bare ACKs - benign, currently a false positive"),
    Scenario("slow_exfil", "203.0.113.44", "detect", "host_outbound", slow_exfil,
             note="sustained upload, ~20:1"),
    Scenario("burst_exfil", "203.0.113.90", "detect", "host_outbound", burst_exfil,
             note="single large upload"),
    Scenario("bulk_download", "150.171.109.74", "reject", "host_outbound", bulk_download,
             note="large inbound - host is receiving"),
    Scenario("stable_heavy", "160.79.104.10", "reject", "endpoints", stable_heavy,
             prior_scales=[1.0, 1.0, 1.0],
             note="heavy every session - own history should demote it"),
    Scenario("behavioral_change", "160.79.104.10", "detect", "endpoints", stable_heavy,
             prior_scales=[0.03, 0.03, 0.03],
             note="quiet for 3 sessions, then 30x - only findable with a baseline"),
]
