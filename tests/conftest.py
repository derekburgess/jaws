"""Shared builders for the JAWS test suite.

Everything here constructs the plain dicts/arrays the scoring path already consumes,
so the detection tests need no Neo4j, no capture, and no embedding model.
"""

import os
import tempfile

# Matplotlib is imported by the legacy finder module even when tests do not render
# plots. Give containerized/read-only home directories a deterministic writable cache.
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "jaws-matplotlib"))

import numpy as np
import pytest

from jaws.jaws_finder import NUMERIC_FEATURE_NAMES, build_numeric_features


def ep(ip, bo, bi, po, pi, op=3, ip_=3, im=None, icv=None, org="Acme"):
    """One endpoint profile, in the shape build_endpoint_profiles emits."""
    return {
        "ip_address": ip,
        "org": org,
        "hostname": "h",
        "location": "l",
        "endpoint_type": "public",
        "bytes_out": bo,
        "bytes_in": bi,
        "packets_out": po,
        "packets_in": pi,
        "out_peers": op,
        "in_peers": ip_,
        "interval_mean": im,
        "interval_cv": icv,
    }


def hist(sessions, **medians):
    """History entry with explicit per-feature medians (missing -> 0)."""
    full = {name: 0.0 for name in NUMERIC_FEATURE_NAMES}
    full.update(medians)
    return {"sessions": sessions, "medians": full}


def history_from(profiles_per_session):
    """Build a history map the way fetch_endpoint_history would, from raw profiles."""
    by_ip = {}
    for profiles in profiles_per_session:
        feats = build_numeric_features(profiles)
        for p, v in zip(profiles, feats):
            by_ip.setdefault(p["ip_address"], []).append(v)
    return {
        ip: {
            "sessions": len(v),
            "medians": {
                n: float(np.median(np.vstack(v)[:, j])) for j, n in enumerate(NUMERIC_FEATURE_NAMES)
            },
        }
        for ip, v in by_ip.items()
    }


def jitter(profiles, k):
    """Realistic session-to-session variation — no two captures are identical."""
    rng = np.random.default_rng(1000 + k)
    out = []
    for p in profiles:
        f = rng.uniform(0.75, 1.35, size=4)
        out.append(
            ep(
                p["ip_address"],
                int(p["bytes_out"] * f[0]),
                int(p["bytes_in"] * f[1]),
                int(p["packets_out"] * f[2]),
                int(p["packets_in"] * f[3]),
                p["out_peers"],
                p["in_peers"],
                p["interval_mean"],
                p["interval_cv"],
            )
        )
    return out


def no_clusters(n):
    """DBSCAN labels for n endpoints, all in one cluster (scores are what we assert)."""
    return np.zeros(n, dtype=int)


def score_of(ranked, ip):
    return next(e for e in ranked if e["ip_address"] == ip)["anomaly_score"]


def row_of(ranked, ip):
    return next(e for e in ranked if e["ip_address"] == ip)


def rank_of(ranked, ip):
    return [e["ip_address"] for e in ranked].index(ip)


@pytest.fixture
def pack():
    """A boring, homogeneous population of 12 endpoints."""
    return [ep(f"10.0.0.{i}", 1000 + i * 10, 2000 + i * 7, 10 + i, 20 + i) for i in range(12)]


@pytest.fixture
def heavy():
    """An endpoint that always moves ~50x the pack."""
    return ep("10.0.0.99", 500_000, 4000, 400, 40)


@pytest.fixture
def pack_history(pack):
    """Three prior sessions of the pack alone, with realistic jitter."""
    return history_from([jitter(pack, 50 + k) for k in range(3)])


@pytest.fixture
def heavy_history(pack):
    """Three prior sessions of the pack plus a consistently heavy endpoint."""
    return history_from(
        [jitter(pack + [ep("10.0.0.99", 495_000, 4000, 398, 40)], k) for k in range(3)]
    )
