"""Plant a labeled scenario, score it with the real detector, report where it landed.

The metric is RANK, not the DBSCAN verdict: `eps` is auto-derived per run (it moved
3.17 -> 1.91 -> 3.68 across three live captures on 2026-07-25), so outlier counts are
not comparable between runs while rank is.
"""

import numpy as np
import pandas as pd

from jaws.jaws_compute import build_endpoint_profiles
from jaws.jaws_finder import (
    NUMERIC_FEATURE_NAMES,
    build_numeric_features,
    score_endpoints,
    score_host_outbound,
)

from .scenarios import HOST, WINDOW, background_packets


def _frame(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "src_ip",
            "dst_ip",
            "src_port",
            "dst_port",
            "size",
            "protocol",
            "ts_ms",
            "capture_id",
        ],
    )


def _profiles(rows):
    return build_endpoint_profiles(_frame(rows), {})


def _history(scenario, seed=7):
    """Prior-session medians, built by profiling real generated sessions.

    Sessions are generated at distinct t0 offsets and stamped with the same synthetic
    capture id per session, so endpoint_timing never pools intervals across the gap.
    """
    if not scenario.prior_scales:
        return None
    per_session = []
    for k, scale in enumerate(scenario.prior_scales):
        t0 = -WINDOW * (len(scenario.prior_scales) - k) - 3600.0
        rows = background_packets(t0=t0, seed=seed + k)
        rows += scenario.packets(t0=t0, scale=scale)
        for r in rows:
            r["capture_id"] = f"PRIOR{k}"
        per_session.append(_profiles(rows))

    by_ip = {}
    for profiles in per_session:
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


def _host_outbound_rows(rows):
    """Recreate the host-frame view fetch_host_outbound builds from raw packets."""
    df = _frame(rows)
    out = (
        df[df["src_ip"] == HOST]
        .groupby("dst_ip")
        .agg(upload_bytes=("size", "sum"), upload_packets=("size", "count"))
    )
    inb = (
        df[df["dst_ip"] == HOST]
        .groupby("src_ip")
        .agg(download_bytes=("size", "sum"), download_packets=("size", "count"))
    )
    joined = out.join(inb, how="outer").fillna(0)
    return [
        {
            "ip_address": ip,
            "org": "synthetic",
            "hostname": None,
            "location": None,
            "cloud_hosted": False,
            "upload_bytes": int(r.upload_bytes),
            "upload_packets": int(r.upload_packets),
            "download_bytes": int(r.download_bytes),
            "download_packets": int(r.download_packets),
        }
        for ip, r in joined.iterrows()
    ]


def evaluate(scenario, seed=7):
    """Returns rank, total, score, reasons for the planted endpoint on its surface."""
    rows = background_packets(seed=seed) + scenario.packets()

    if scenario.surface == "host_outbound":
        ranked = score_host_outbound(_host_outbound_rows(rows))
        key = "outbound_score"
    else:
        profiles = _profiles(rows)
        ranked = score_endpoints(
            profiles, np.zeros(len(profiles), dtype=int), _history(scenario, seed)
        )
        key = "anomaly_score"

    ips = [e["ip_address"] for e in ranked]
    if scenario.planted_ip not in ips:
        return {
            "scenario": scenario,
            "rank": None,
            "total": len(ips),
            "score": None,
            "reasons": [],
            "passed": False,
        }

    rank = ips.index(scenario.planted_ip)
    row = ranked[rank]
    # "detect" means top-3; "reject" means it must NOT crowd the top of the ranking.
    passed = rank < 3 if scenario.expect == "detect" else rank >= 3
    return {
        "scenario": scenario,
        "rank": rank,
        "total": len(ips),
        "score": row[key],
        "reasons": [r["feature"] for r in row["reasons"]],
        "passed": passed,
    }


def report(results):
    """Human-readable recall summary."""
    detect = [r for r in results if r["scenario"].expect == "detect"]
    reject = [r for r in results if r["scenario"].expect == "reject"]
    lines = [
        f"{'scenario':<20} {'expect':<7} {'surface':<14} {'rank':>6} {'score':>8}  reasons",
        "-" * 92,
    ]
    for r in sorted(results, key=lambda x: (x["scenario"].expect, x["scenario"].name)):
        s = r["scenario"]
        rank = "absent" if r["rank"] is None else f"{r['rank'] + 1}/{r['total']}"
        score = "-" if r["score"] is None else f"{r['score']:.2f}"
        mark = "ok " if r["passed"] else "FAIL"
        lines.append(
            f"{mark} {s.name:<16} {s.expect:<7} {s.surface:<14} "
            f"{rank:>6} {score:>8}  {','.join(r['reasons']) or '-'}"
        )
    hits = sum(1 for r in detect if r["passed"])
    fps = sum(1 for r in reject if not r["passed"])
    lines += ["-" * 92, f"recall@3 {hits}/{len(detect)}    false positives {fps}/{len(reject)}"]
    return "\n".join(lines)
