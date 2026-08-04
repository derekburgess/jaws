import argparse
import os
import tempfile

import numpy as np
from kneed import KneeLocator
from sklearn.cluster import DBSCAN
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from jaws.config import DATABASE, FINDER_ENDPOINT, is_cloud_hosted
from jaws.jaws_utils import (
    MIN_TIMING_PACKETS,
    NON_CONVERSATIONAL_TYPES,
    Reporter,
    classify_endpoint,
    dbms_connection,
)
from jaws.optional_dependencies import require_module

plt = None
plotille = None


def _load_plotting():
    """Load plotting libraries only for rendering paths, never numeric imports."""

    global plt, plotille
    if plt is None:
        plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    if plotille is None:
        plotille = require_module("plotille", "plotting", "Terminal plot rendering")
    return plt, plotille


# Behavioral features blended with the text embedding so volume/fan-out anomalies
# (e.g. unusual outbound bytes) separate geometrically — text embeddings alone
# barely encode magnitude. Log-scaled (they span orders of magnitude) then
# standardized to unit variance, matching the standardized text components.
BASE_FEATURES = ["bytes_out", "bytes_in", "packets_out", "packets_in", "out_peers", "in_peers"]

# Derived shape features. Raw counts mostly re-flag the busiest host; ratios encode
# *shape* instead of volume, so they catch exfil (out >> in), beaconing (small constant
# packets), and low-fan-out chatter regardless of absolute size. The +1 denominators
# keep them finite when a direction is empty.
RATIO_FEATURES = {
    "bytes_out_in_ratio": lambda d: d["bytes_out"] / (d["bytes_in"] + 1.0),
    "packets_out_in_ratio": lambda d: d["packets_out"] / (d["packets_in"] + 1.0),
    "bytes_per_packet": lambda d: (
        (d["bytes_out"] + d["bytes_in"]) / (d["packets_out"] + d["packets_in"] + 1.0)
    ),
    "bytes_per_peer": lambda d: d["bytes_out"] / (d["out_peers"] + 1.0),
}

# Temporal cadence features (set by jaws_compute, from the endpoint's more regular
# single DIRECTION — a combined stream's request/response pairing forces CV toward 1.0
# and hides real beacons). INTERVAL_CV — the coefficient of variation of inter-packet
# gaps — is the beaconing signal: a low CV means highly regular callbacks (C2-like), a
# high CV means bursty/human traffic. INTERVAL_MEAN is the typical period. Endpoints
# with too few packets in each direction carry None and are median-imputed below so
# they read as "average regularity" rather than as perfect beacons.
TIMING_FEATURES = ["interval_mean", "interval_cv"]

NUMERIC_FEATURE_COUNT = len(BASE_FEATURES) + len(RATIO_FEATURES) + len(TIMING_FEATURES)

# Column order of build_numeric_features (base, then ratios, then timing), so a
# robust-z column index maps back to the feature it scored.
NUMERIC_FEATURE_NAMES = BASE_FEATURES + list(RATIO_FEATURES) + TIMING_FEATURES

# Human-readable unit per numeric feature, surfaced in the result so the raw
# magnitudes aren't left unlabeled (bytes_out is bytes, interval_mean is seconds,
# the coefficient of variation and the out/in ratios are dimensionless).
FEATURE_UNITS = {
    "bytes_out": "bytes",
    "bytes_in": "bytes",
    "packets_out": "packets",
    "packets_in": "packets",
    "out_peers": "peers",
    "in_peers": "peers",
    "bytes_out_in_ratio": "ratio",
    "packets_out_in_ratio": "ratio",
    "bytes_per_packet": "bytes/packet",
    "bytes_per_peer": "bytes/peer",
    "interval_mean": "seconds",
    "interval_cv": "ratio",
}

# A feature must deviate by at least this robust-z to be cited as a reason an
# endpoint was anomalous. ~2.5 robust deviations is a clear departure from the pack
# without naming every minor wobble.
REASON_Z_THRESHOLD = 2.5

# The anomaly score aggregates each endpoint's robust-z vector as the L2 norm of its
# SCORE_TOP_K largest |z| components, not the full vector. About half the numeric
# features are correlated through bytes_out, so a full-vector norm let a silent
# one-packet endpoint stack six mild "low" deviations and outrank a genuine
# single-feature spike; top-k keeps a multi-feature anomaly ahead of a single-feature
# one without rewarding that redundancy.
SCORE_TOP_K = 3

# "Low" deviations (quieter than the pack) are weak threat signal compared to "high"
# ones — and they are exactly what correlated features stack — so each one's score
# contribution is down-weighted AND capped. The cap matters more than the weight: on a
# homogeneous pack the MAD is tiny, so a silent one-packet endpoint's lows reach
# robust-z ≈ −40 per feature and no linear weight tames that. "Quieter than typical"
# saturates as a signal, so a low contributes at most a clear-deviation's worth
# (~REASON_Z_THRESHOLD); with SCORE_TOP_K lows the score tops out near
# sqrt(SCORE_TOP_K) * cap ≈ 5.2, below any genuinely strong single high. EXCEPT where
# low is itself the signal: a low interval_cv is the beaconing indicator and keeps
# full, uncapped weight. Reasons still cite the full unweighted robust-z in both
# directions; the weighting shapes only the ranking.
LOW_DIRECTION_WEIGHT = 0.5
LOW_DIRECTION_CAP = 3.0
LOW_SIGNAL_FEATURES = {"interval_cv"}

# Features whose deviations saturate in BOTH directions. interval_mean is cadence —
# context, not signal (interval_cv carries the beacon indicator) — and the population
# median interval is typically sub-second with a tiny MAD, so a benign endpoint polling
# every few seconds lands at robust-z 10-17 and owns the top of the ranking on cadence
# alone. "Slower cadence than the pack" saturates exactly like "quieter than the pack"
# does: cap its score contribution (full weight, same cap as lows) so timing context
# can't outrank a genuine volume/shape spike. Reasons still cite the raw robust-z.
SATURATING_FEATURES = {"interval_mean"}


# Historical baselining: compare an endpoint against ITS OWN past sessions rather than
# only against its current peers. Profile sets accumulate one per capture session (see
# jaws_compute), so an IP that has been profiled before has a per-feature history. The
# median of that history becomes the endpoint's expected value, and the robust-z then
# measures departure from it — so a backup server that always moves 2 GB stops owning the
# ranking on volume it posts every single session, while the same 2 GB from a host that
# has never sent more than a megabyte is a genuine spike. Endpoints without enough history
# fall back to the column median, i.e. exactly the peer-relative behavior.
#
# A single prior observation is a noisy "median" over a 30-120s capture, so require two.
MIN_BASELINE_SESSIONS = 2

# Features NEVER baselined against an endpoint's own history — the trap that historical
# baselining walks into. Consistency is precisely what makes a beacon a beacon: an
# implant calling home every 60s looks identical in every session, so its own history
# would declare it perfectly normal and silence the strongest signal the tool has.
# Cadence stays peer-relative, permanently. Volume and shape features are the ones where
# "this endpoint always does this" is genuinely uninteresting.
BASELINE_EXEMPT_FEATURES = set(TIMING_FEATURES)


def build_baseline_centers(data, history, feature_names, raw):
    """Per-row expected values for the robust-z, from each endpoint's own history.

    `history` maps ip_address -> per-feature median over that IP's PRIOR profile sets
    (see fetch_endpoint_history), already in raw units. Returns
    (centers, baseline_sessions) where `centers` is a log1p-scaled matrix shaped like
    `raw` — the endpoint's historical median where it has at least MIN_BASELINE_SESSIONS
    prior sessions and the feature is not baseline-exempt, otherwise the column median,
    which reproduces the un-baselined score for that cell — and `baseline_sessions` is
    the per-row count of prior sessions backing the endpoint's baseline (0 = none, so
    that row is purely peer-relative).

    Mixing baselined and un-baselined cells in one column is coherent because log1p is
    monotonic: median(log1p(x)) == log1p(median(x)), so an un-baselined cell's center IS
    the value robust_z_scores would have subtracted anyway. Only the center moves.
    """
    x = np.log1p(raw)
    centers = np.tile(np.median(x, axis=0), (len(data), 1))
    baseline_sessions = np.zeros(len(data), dtype=int)
    if not history:
        return centers, baseline_sessions

    exempt = [name in BASELINE_EXEMPT_FEATURES for name in feature_names]
    for i, item in enumerate(data):
        entry = history.get(item["ip_address"])
        if entry is None or entry["sessions"] < MIN_BASELINE_SESSIONS:
            continue
        baseline_sessions[i] = entry["sessions"]
        for j, name in enumerate(feature_names):
            if exempt[j]:
                continue
            value = entry["medians"].get(name)
            if value is not None and np.isfinite(value):
                centers[i, j] = np.log1p(max(float(value), 0.0))
    return centers, baseline_sessions


def deviation_score(z, feature_names):
    """One rankable score per row of a robust-z matrix.

    L2 norm over the SCORE_TOP_K largest |z| per row, with negative deviations
    down-weighted by LOW_DIRECTION_WEIGHT and capped at LOW_DIRECTION_CAP, except for
    LOW_SIGNAL_FEATURES which count like highs, and SATURATING_FEATURES capped in both
    directions (see the constants above for why this replaces a full-vector norm).
    Columns align with `feature_names`.
    """
    low_signal = np.array([name in LOW_SIGNAL_FEATURES for name in feature_names])
    is_capped_low = (z < 0) & ~low_signal
    weighted = np.abs(z)
    weighted[is_capped_low] = np.minimum(
        weighted[is_capped_low] * LOW_DIRECTION_WEIGHT, LOW_DIRECTION_CAP
    )
    saturating = np.array([name in SATURATING_FEATURES for name in feature_names])
    weighted[:, saturating] = np.minimum(weighted[:, saturating], LOW_DIRECTION_CAP)
    k = min(SCORE_TOP_K, weighted.shape[1])
    top = np.sort(weighted, axis=1)[:, -k:]
    return np.sqrt(np.sum(top**2, axis=1))


# The capture host's own IP is owned by this synthetic org (see initialize_schema).
# It is a structural hub — it talks to every peer, so it dominates behavioral clustering
# — and is excluded from the clustered set by default. Its outbound traffic still shows
# up as each remote endpoint's inbound, so outbound anomalies remain detectable.
LOCAL_ORG = "YOU ARE HERE"


# An endpoint's *_out / *_in counts are from ITS OWN perspective: bytes_out is what the
# endpoint sent, bytes_in what it received. Because the capture host is excluded from
# clustering by default, every scored endpoint is REMOTE — and a remote endpoint's
# bytes_out is data it sent TO the host, i.e. traffic the host DOWNLOADED, the opposite of
# exfiltration. Read naively, "bytes_out high" inverts the threat model (the tool's concern
# is outbound *from the host*). These maps re-state each directional feature in the capture
# host's frame so a reason can't be misread; the local host, when included, is its own frame
# (its perspective IS the host's). Keyed by flow: "out" = endpoint-as-source, "in" = -as-dest.
HOST_FRAME_REMOTE = {
    "out": "remote → host: traffic the capture host downloaded (NOT host exfil)",
    "in": "host → remote: traffic the capture host sent to this IP (outbound-from-host signal)",
}
HOST_FRAME_LOCAL = {
    "out": "host → network: traffic the capture host sent out (outbound-from-host signal)",
    "in": "network → host: traffic the capture host received (download)",
}

# Which directional flow each feature belongs to, for host-frame glossing. "out" features
# count the endpoint as source, "in" as destination; the out/in ratios and bytes_per_peer
# are out-dominant when high. Features absent here (bytes_per_packet, peers, timing) carry
# no host-relative direction and get no gloss.
FEATURE_FLOW = {
    "bytes_out": "out",
    "packets_out": "out",
    "bytes_in": "in",
    "packets_in": "in",
    "bytes_out_in_ratio": "out",
    "packets_out_in_ratio": "out",
    "bytes_per_peer": "out",
}


def host_relative_gloss(feature, is_local):
    """Defender-frame interpretation of a directional feature, or None if it has none.

    Disambiguates a flagged feature relative to the capture host so an agent doesn't read a
    remote endpoint's bytes_out (a host download) as exfiltration. See HOST_FRAME_* above.
    """
    flow = FEATURE_FLOW.get(feature)
    if flow is None:
        return None
    return (HOST_FRAME_LOCAL if is_local else HOST_FRAME_REMOTE)[flow]


def build_numeric_features(data):
    """Assemble the raw numeric matrix: base counts + derived ratios + timing.

    Timing columns may contain None (sparse endpoints); each is imputed with the
    median of its present values so missingness reads as neutral, not anomalous.
    Returns a float array of shape (n_endpoints, NUMERIC_FEATURE_COUNT).
    """
    base = [[float(d[f]) for f in BASE_FEATURES] for d in data]
    ratios = [[fn(d) for fn in RATIO_FEATURES.values()] for d in data]

    timing_cols = []
    for f in TIMING_FEATURES:
        col = [d.get(f) for d in data]
        present = [v for v in col if v is not None]
        median = float(np.median(present)) if present else 0.0
        timing_cols.append([float(v) if v is not None else median for v in col])
    timing = np.array(timing_cols).T if timing_cols else np.empty((len(data), 0))

    return np.hstack([np.array(base), np.array(ratios), timing])


def _robust_center_scale(x):
    """Per-column (median, scale) for robust z, with the degenerate cases handled.

    median/MAD rather than mean/std so the location and scale aren't dragged toward the
    very outlier being measured. Where MAD is ~0 (e.g. many identical median-imputed
    timing values) it falls back to the standard deviation, and to 0 for a genuinely
    constant column — both avoid the div-by-zero infinities a naive MAD-z produces.
    """
    median = np.median(x, axis=0)
    mad = np.median(np.abs(x - median), axis=0)
    scale = 1.4826 * mad
    scale = np.where(scale > 1e-9, scale, x.std(axis=0))
    return median, scale


def robust_z_scores(raw):
    """Per-column robust z-scores: (x - median) / (1.4826 * MAD), on log1p-scaled
    features.

    log1p first so multiplicative spread (bytes span orders of magnitude) reads on a
    single scale and one busy host doesn't swamp every column. Returns an array shaped
    like `raw`, columns aligned with NUMERIC_FEATURE_NAMES.
    """
    x = np.log1p(raw)
    median, scale = _robust_center_scale(x)
    z = np.zeros_like(x)
    usable = scale > 1e-9
    z[:, usable] = (x[:, usable] - median[usable]) / scale[usable]
    return z


# Smallest residual spread treated as meaningful in the HISTORICAL frame, on the log1p
# scale (~0.1 ≈ a 10% change). Only reached when the baselined endpoints are so stable
# that the MAD of their residuals collapses toward zero — at which point the scale would
# be set by the single endpoint that did change, normalizing its own deviation away and
# making a 20x departure score the same as a 1% one. The floor says "changes below ~10%
# are noise" and restores magnitude sensitivity. Never applied to the peer frame, whose
# scaling is tuned and must not shift.
BASELINE_SCALE_FLOOR = 0.1


def baselined_z_scores(raw, centers, baselined, feature_names):
    """Robust-z where each endpoint is measured in the reference frame it has earned.

    Rows with usable history are scored on their residual from their OWN baseline
    (log1p(x) - center); every other row keeps the peer-relative score, unchanged. The
    two frames are standardized SEPARATELY: a residual-from-own-past and a
    deviation-from-the-pack have genuinely different spreads, and pooling them into one
    column scale lets either corrupt the other (a population of stable endpoints drives
    the shared MAD toward zero and inflates every peer-scored row). Columns in
    `BASELINE_EXEMPT_FEATURES` stay peer-relative for every row.

    Within the historical frame the residuals are re-centered on their own median before
    scaling, so a shift affecting the whole population — a 120s capture following a 30s
    one scales every volume feature — cancels instead of flagging everything. What
    survives is endpoint-specific change.
    """
    z = robust_z_scores(raw)
    if not baselined.any():
        return z
    columns = [j for j, name in enumerate(feature_names) if name not in BASELINE_EXEMPT_FEATURES]
    if not columns:
        return z

    residual = np.log1p(raw) - centers
    median, scale = _robust_center_scale(residual[baselined])
    scale = np.maximum(scale, BASELINE_SCALE_FLOOR)
    z_history = (residual - median) / scale
    z[np.ix_(baselined, columns)] = z_history[np.ix_(baselined, columns)]
    return z


def score_endpoints(data, clusters, history=None):
    """Attach a rankable anomaly score and reason codes to every endpoint.

    `anomaly_score` is the deviation_score of the endpoint's per-feature robust-z
    vector (top-k, low-direction-weighted L2 — see deviation_score) — its behavioral
    distance from the pack — so endpoints that deviate strongly on a few features
    outrank those that are mildly odd on many correlated ones, and the score exists
    even when DBSCAN flags nothing. `reasons` cites the features whose |robust-z|
    clears REASON_Z_THRESHOLD, each with its raw value, unit, and direction, turning
    'flagged' into 'flagged because bytes_out is far above the typical host'.
    `is_outlier` carries the DBSCAN verdict so the geometric flag and the
    interpretable score coexist. Returns the full list sorted by score descending.

    `history` (from fetch_endpoint_history) switches each feature's reference point from
    the current pack to the endpoint's own past where it has one — so a reason reads
    'far above what THIS endpoint normally does' rather than 'far above its peers'. Each
    reason names which comparison produced it via `compared_to`, and carries the
    `baseline` it was measured against when that was the endpoint's history. Passing
    None (or an empty history) reproduces the purely peer-relative score exactly.
    """
    raw = build_numeric_features(data)
    centers, baseline_sessions = build_baseline_centers(
        data, history or {}, NUMERIC_FEATURE_NAMES, raw
    )
    baselined_rows = baseline_sessions >= MIN_BASELINE_SESSIONS
    baselined = bool(history) and bool(baselined_rows.any())
    z = (
        baselined_z_scores(raw, centers, baselined_rows, NUMERIC_FEATURE_NAMES)
        if baselined
        else robust_z_scores(raw)
    )

    # Timing z-scores are only meaningful with enough intervals behind them: profiles
    # computed under the old MIN_TIMING_PACKETS gate (3) carry an interval_cv from a
    # 2-interval burst, so a lone handshake reads as cv ≈ 0.33 — "beacon-like" — for a
    # benign CDN. Timing is now per direction, so packets_out + packets_in below the
    # gate guarantees neither direction met it; neutralize timing there so old graphs
    # are fixed without re-computing (new computes leave timing None below the gate).
    timing_idx = [NUMERIC_FEATURE_NAMES.index(f) for f in TIMING_FEATURES]
    for i, item in enumerate(data):
        if item["packets_out"] + item["packets_in"] < MIN_TIMING_PACKETS:
            z[i, timing_idx] = 0.0

    scores = deviation_score(z, NUMERIC_FEATURE_NAMES)

    ranked = []
    for i, item in enumerate(data):
        # The clustered set is remote by default; a remote endpoint's *_out is data it sent
        # TO the host (a download). is_local flips the host-frame gloss for the capture host.
        is_local = item["org"] == LOCAL_ORG
        # Whether this row's cells were measured against the endpoint's own history or
        # against its peers — per row, since an endpoint new in this session has no
        # baseline even when the rest of the set does.
        row_baselined = baselined and bool(baselined_rows[i])
        reasons = []
        for j, name in enumerate(NUMERIC_FEATURE_NAMES):
            zj = float(z[i, j])
            if abs(zj) >= REASON_Z_THRESHOLD:
                against_history = row_baselined and name not in BASELINE_EXEMPT_FEATURES
                reason = {
                    "feature": name,
                    "value": round(float(raw[i, j]), 4),
                    "unit": FEATURE_UNITS[name],
                    "robust_z": round(zj, 2),
                    "direction": "high" if zj > 0 else "low",
                    # What the deviation is relative to. Without this an agent cannot tell
                    # "unusual for the network" from "unusual for this endpoint" — two
                    # findings that warrant different responses.
                    "compared_to": ("own history" if against_history else "peer endpoints"),
                }
                if against_history:
                    reason["baseline"] = round(
                        float(history[item["ip_address"]]["medians"][name]), 4
                    )
                    reason["baseline_sessions"] = int(baseline_sessions[i])
                # Defender-frame disambiguation so "bytes_out high" on a remote IP reads as
                # a host download, not exfil (omitted for non-directional features).
                gloss = host_relative_gloss(name, is_local)
                if gloss:
                    reason["host_relative"] = gloss
                reasons.append(reason)
        reasons.sort(key=lambda r: abs(r["robust_z"]), reverse=True)
        ranked.append(
            {
                "ip_address": item["ip_address"],
                "endpoint_type": item.get("endpoint_type"),
                "org": item["org"],
                # Hosting/CDN ASNs label the infrastructure provider, not the actual
                # service — keep "org: Google LLC" on a GCP customer VM from reading as
                # Google's own reputation.
                "cloud_hosted": is_cloud_hosted(item["org"]),
                "hostname": item["hostname"],
                "location": item["location"],
                "bytes_out": item["bytes_out"],
                "packets_out": item["packets_out"],
                "bytes_in": item["bytes_in"],
                "packets_in": item["packets_in"],
                "interval_mean": item["interval_mean"],
                "interval_cv": item["interval_cv"],
                "anomaly_score": round(float(scores[i]), 4),
                "is_outlier": bool(clusters[i] == -1),
                # How many earlier sessions this IP was profiled in, and whether this session
                # is the first time it has ever been seen. A never-before-seen endpoint is a
                # finding in its own right that no per-feature z can express — the features
                # only describe what it did, not that it is new. None when there is no prior
                # history at all (nothing is "new" against an empty graph).
                "baseline_sessions": int(baseline_sessions[i]),
                "first_seen": (None if not history else item["ip_address"] not in history),
                "reasons": reasons,
            }
        )
    ranked.sort(key=lambda e: e["anomaly_score"], reverse=True)
    return ranked


# The host-outbound view answers the tool's stated purpose directly — "unusual outbound
# traffic FROM the capture host." Clustering excludes the host (a structural hub), so its
# real outbound is scattered as small bytes_in across many remote endpoints and never
# dominates an anomaly_score; the remote-endpoint ranking is driven by inbound/download
# volume instead. This view re-centers on the host: for every destination the host SENT to,
# it measures the host's own upload (host as packet SOURCE, computed from raw packets so it
# isolates host→peer flow rather than the peer's total inbound from all sources) and ranks
# destinations on that distribution. upload_download_ratio >> 1 is the exfil shape.
HOST_OUTBOUND_FEATURES = ["upload_bytes", "upload_packets", "upload_download_ratio"]
HOST_OUTBOUND_UNITS = {
    "upload_bytes": "bytes",
    "upload_packets": "packets",
    "upload_download_ratio": "ratio",
}
# All three features are host→remote outbound, so every reason is unambiguously the
# outbound-from-host signal (no perspective flip needed — this view is already host-framed).
HOST_OUTBOUND_GLOSS = {
    "upload_bytes": "host → remote: data the capture host sent out (outbound from host)",
    "upload_packets": "host → remote: packets the capture host sent out (outbound from host)",
    "upload_download_ratio": "host sent more to this peer than it received (exfil-shaped when high)",
}


def score_host_outbound(rows):
    """Rank the host's outbound destinations on the host-upload distribution.

    `rows` is one dict per destination the host sent to (upload/download bytes & packets).
    Adds `upload_download_ratio`, an `outbound_score` (deviation_score of the robust-z
    vector over the host-upload features — same aggregation as score_endpoints, but on
    the host's OWN outbound rather than a remote's perspective), and host-frame
    `reasons`. Returns the list sorted by outbound_score descending; an empty input
    yields an empty list.
    """
    if not rows:
        return []
    for r in rows:
        r["upload_download_ratio"] = r["upload_bytes"] / (r["download_bytes"] + 1.0)
    raw = np.array([[float(r[f]) for f in HOST_OUTBOUND_FEATURES] for r in rows])
    z = robust_z_scores(raw)
    scores = deviation_score(z, HOST_OUTBOUND_FEATURES)

    ranked = []
    for i, r in enumerate(rows):
        reasons = []
        for j, name in enumerate(HOST_OUTBOUND_FEATURES):
            zj = float(z[i, j])
            if abs(zj) >= REASON_Z_THRESHOLD:
                reasons.append(
                    {
                        "feature": name,
                        "value": round(float(raw[i, j]), 4),
                        "unit": HOST_OUTBOUND_UNITS[name],
                        "robust_z": round(zj, 2),
                        "direction": "high" if zj > 0 else "low",
                        "host_relative": HOST_OUTBOUND_GLOSS[name],
                    }
                )
        reasons.sort(key=lambda x: abs(x["robust_z"]), reverse=True)
        ranked.append(
            {
                "ip_address": r["ip_address"],
                "org": r["org"],
                "cloud_hosted": is_cloud_hosted(r["org"]),
                "hostname": r["hostname"],
                "location": r["location"],
                "upload_bytes": r["upload_bytes"],
                "upload_packets": r["upload_packets"],
                "download_bytes": r["download_bytes"],
                "download_packets": r["download_packets"],
                "upload_download_ratio": round(float(r["upload_download_ratio"]), 4),
                "outbound_score": round(float(scores[i]), 4),
                # Explicit per-row verdict (a reason cleared REASON_Z_THRESHOLD), so the
                # top-level `flagged` count is joinable without inferring from `reasons`.
                "is_flagged": bool(reasons),
                "reasons": reasons,
            }
        )
    ranked.sort(key=lambda e: e["outbound_score"], reverse=True)
    return ranked


def _whole_number_formatter(val, chars, delta, left=False):
    # plotille label formatter: render axis tick labels as whole numbers instead of
    # full float precision (e.g. 4 rather than 3.73886505).
    s = f"{val:.0f}"
    return f"{s:<{chars}}" if left else f"{s:>{chars}}"


def new_plotille_figure():
    _load_plotting()
    fig = plotille.Figure()
    fig.register_label_formatter(float, _whole_number_formatter)
    return fig


def build_feature_matrix(embeddings, data, components, whiten, feature_weight):
    """Combine text-embedding PCA components with standardized numeric features.

    The text block is kept at its natural PCA scale — deliberately NOT re-standardized,
    so when the descriptions are homogeneous (little real text variance) the text
    contributes little instead of having its noise amplified to unit scale. The numeric
    block is log-scaled and standardized to unit variance, and `feature_weight` scales it
    relative to the text (0.0 = embedding-only / original behavior, higher = more
    volume/fan-out influence). Returns (features_for_clustering, pca_object).
    """
    embeddings_array = np.array(embeddings)
    pca = PCA(n_components=components, whiten=whiten)
    text_block = pca.fit_transform(embeddings_array)

    if feature_weight <= 0:
        return text_block, pca

    raw = build_numeric_features(data)
    numeric_block = StandardScaler().fit_transform(np.log1p(raw))
    features = np.hstack([text_block, feature_weight * numeric_block])
    return features, pca


# The pooled 'all' scope is not a point in time — it re-aggregates every packet in the
# graph, so it both overlaps every real session and has no position in the sequence.
# It can be analyzed, but it can never serve as (or receive) a historical baseline.
POOLED_SCOPE = "all"


def resolve_profile_scope(driver, database, session_arg):
    """Pick which stored profile set (one per compute run) to analyze.

    Profile sets accumulate — one ENDPOINT per IP per session — so unlike the old
    single-generation layer the finder must say which one it means. Returns
    (scope, available) where `scope` is a CAPTURE_ID, 'all', or None on a legacy graph
    whose profiles predate session stamping; `available` is every profiled scope, most
    recently computed first. 'latest' picks the most recently COMPUTED set (by profile
    TIMESTAMP), which is what the pipeline just produced. Raises ValueError for an
    explicit scope that has no profiles.
    """
    query = """
    MATCH (e:ENDPOINT)
    RETURN e.CAPTURE_ID AS scope, max(e.TIMESTAMP) AS computed, count(e) AS endpoints
    ORDER BY computed DESC
    """
    with driver.session(database=database) as session:
        rows = [record.data() for record in session.run(query)]
    available = [r["scope"] for r in rows]
    if not available:
        return None, []
    if session_arg == "latest":
        return available[0], available
    if session_arg not in available:
        raise ValueError(
            f"no endpoint profiles for session '{session_arg}'; profiled sessions: {available}. "
            f"Run jaws-compute --session {session_arg} first."
        )
    return session_arg, available


# Every numeric input the baseline needs, read from the profile sets that PRECEDE the one
# being analyzed. Ordering is lexicographic on CAPTURE_ID, which is chronological by
# construction (compact UTC timestamps), so "prior" stays correct when an older session is
# re-profiled after a newer one.
_HISTORY_QUERY = """
MATCH (e:ENDPOINT)
WHERE e.CAPTURE_ID IS NOT NULL
  AND e.CAPTURE_ID <> $scope
  AND e.CAPTURE_ID <> $pooled
  AND ($scope = $pooled OR e.CAPTURE_ID < $scope)
RETURN e.IP_ADDRESS AS ip_address,
       e.CAPTURE_ID AS capture_id,
       e.BYTES_OUT AS bytes_out,
       e.PACKETS_OUT AS packets_out,
       e.OUT_PEERS AS out_peers,
       e.BYTES_IN AS bytes_in,
       e.PACKETS_IN AS packets_in,
       e.IN_PEERS AS in_peers,
       e.INTERVAL_MEAN AS interval_mean,
       e.INTERVAL_CV AS interval_cv
"""


def fetch_endpoint_history(driver, database, scope):
    """Per-IP feature medians over the profile sets preceding `scope`.

    Returns ip_address -> {"sessions": n, "medians": {feature: value}}, where the
    features are the same NUMERIC_FEATURE_NAMES the live set is scored on (derived
    ratios included, computed through build_numeric_features so history and present are
    assembled identically). An IP absent from the map has never been profiled before —
    the `first_seen` case.
    """
    if scope is None:
        return {}
    with driver.session(database=database) as session:
        rows = [
            record.data()
            for record in session.run(_HISTORY_QUERY, scope=scope, pooled=POOLED_SCOPE)
        ]
    if not rows:
        return {}

    for row in rows:
        for key in ("bytes_out", "packets_out", "out_peers", "bytes_in", "packets_in", "in_peers"):
            row[key] = row[key] or 0
    features = build_numeric_features(rows)

    by_ip = {}
    for row, vector in zip(rows, features):
        by_ip.setdefault(row["ip_address"], []).append(vector)
    history = {}
    for ip, vectors in by_ip.items():
        stacked = np.vstack(vectors)
        history[ip] = {
            "sessions": len(vectors),
            "medians": {
                name: float(np.median(stacked[:, j]))
                for j, name in enumerate(NUMERIC_FEATURE_NAMES)
            },
        }
    return history


def fetch_data_for_dbscan(driver, database, include_local=False, scope=None):
    # Scoped to ONE profile set: profiles accumulate per session, so an unscoped match
    # would return the same IP once per session it was ever seen in and cluster an
    # endpoint against its own past selves. scope=None only happens on a legacy graph
    # whose profiles predate session stamping, where one generation is all there is.
    query = """
    MATCH (endpoint:ENDPOINT)
    WHERE $scope IS NULL OR endpoint.CAPTURE_ID = $scope
    OPTIONAL MATCH (ip:IP_ADDRESS {IP_ADDRESS: endpoint.IP_ADDRESS})<-[:OWNERSHIP]-(org:ORGANIZATION)
    RETURN endpoint.IP_ADDRESS AS ip_address,
           endpoint.CAPTURE_ID AS capture_id,
           COALESCE(endpoint.ORGANIZATION, org.ORGANIZATION, 'Unknown') AS org,
           COALESCE(endpoint.HOSTNAME, ip.HOSTNAME, 'Unknown') AS hostname,
           COALESCE(endpoint.LOCATION, ip.LOCATION, 'Unknown') AS location,
           endpoint.BYTES_OUT AS bytes_out,
           endpoint.PACKETS_OUT AS packets_out,
           endpoint.OUT_PEERS AS out_peers,
           endpoint.BYTES_IN AS bytes_in,
           endpoint.PACKETS_IN AS packets_in,
           endpoint.IN_PEERS AS in_peers,
           endpoint.INTERVAL_MEAN AS interval_mean,
           endpoint.INTERVAL_CV AS interval_cv,
           endpoint.EMBEDDING AS embedding
    """
    with driver.session(database=database) as session:
        result = session.run(query, scope=scope)
        embeddings = []
        data = []
        excluded_local = 0
        excluded_non_conversational = []
        for record in result:
            if record["embedding"] is not None:  # Only process endpoints with embeddings
                if not include_local and record["org"] == LOCAL_ORG:
                    excluded_local += 1
                    continue
                ip_address = record["ip_address"] or "Unknown"
                # Multicast/broadcast destinations never reply, so their profiles are
                # one-way protocol chatter (SSDP/mDNS) whose out/in shape reads as
                # exfil. They stay in the graph (tagged, inspectable) but are not
                # clustered or ranked. Classified here from the IP rather than the
                # stored ENDPOINT_TYPE so graphs computed before the tag existed are
                # filtered too.
                endpoint_type = classify_endpoint(ip_address)
                if endpoint_type in NON_CONVERSATIONAL_TYPES:
                    excluded_non_conversational.append(
                        {"ip_address": ip_address, "endpoint_type": endpoint_type}
                    )
                    continue
                embeddings.append(np.array(record["embedding"]))
                data.append(
                    {
                        "ip_address": ip_address,
                        "endpoint_type": endpoint_type,
                        # Session scope this profile was computed from ('all', a concrete
                        # CAPTURE_ID, or None on graphs computed before sessions existed).
                        "capture_id": record["capture_id"],
                        "org": record["org"] or "Unknown",
                        "hostname": record["hostname"] or "Unknown",
                        "location": record["location"] or "Unknown",
                        "bytes_out": record["bytes_out"] or 0,
                        "packets_out": record["packets_out"] or 0,
                        "out_peers": record["out_peers"] or 0,
                        "bytes_in": record["bytes_in"] or 0,
                        "packets_in": record["packets_in"] or 0,
                        "in_peers": record["in_peers"] or 0,
                        # None when the endpoint had too few packets to time — kept as
                        # None so build_numeric_features median-imputes it.
                        "interval_mean": record["interval_mean"],
                        "interval_cv": record["interval_cv"],
                    }
                )
        return embeddings, data, excluded_local, excluded_non_conversational


def fetch_data_for_portsize(driver, database):
    query = """
    MATCH (src_port:PORT)-[:SENT]->(packet:PACKET)-[:RECEIVED]->(dst_port:PORT)
    RETURN packet.SIZE AS size, src_port.PORT AS src_port, dst_port.PORT AS dst_port
    """
    with driver.session(database=database) as session:
        result = session.run(query)
        plot_data = [
            {"size": record["size"], "src_port": record["src_port"], "dst_port": record["dst_port"]}
            for record in result
        ]
    return plot_data


def fetch_host_outbound(driver, database, capture_id=None):
    """Aggregate the capture host's outbound traffic per destination, from raw packets.

    Finds the host IP(s) (owned by LOCAL_ORG), then for every peer the host exchanged
    packets with sums the host's upload (host as SOURCE) and download (host as
    DESTINATION) separately — so each row is the true host→peer flow, not the peer's total
    inbound from every source. Rows are restricted to peers the host actually sent to
    (upload_packets > 0). `capture_id` scopes the packet scan to one capture session so
    this view describes the same traffic the ENDPOINT profiles do; None scans everything.
    Returns (local_ips, rows); local_ips is empty when the host was never captured
    (e.g. an imported pcap with no local endpoint), in which case rows is empty too and
    the caller surfaces an empty host-outbound view rather than crashing.
    """
    local_query = """
    MATCH (org:ORGANIZATION {ORGANIZATION: $local_org})-[:OWNERSHIP]->(ip:IP_ADDRESS)
    RETURN collect(ip.IP_ADDRESS) AS local_ips
    """
    # peer = the non-local side of each packet; outbound = the host was the source. Group
    # by peer, split bytes/packets by direction, then join the peer's OSINT metadata.
    peer_query = """
    MATCH (p:PACKET)
    WHERE (p.SRC_IP IN $local_ips OR p.DST_IP IN $local_ips)
      AND ($capture_id IS NULL OR p.CAPTURE_ID = $capture_id)
    WITH p,
         CASE WHEN p.SRC_IP IN $local_ips THEN p.DST_IP ELSE p.SRC_IP END AS peer,
         (p.SRC_IP IN $local_ips) AS outbound
    WHERE NOT peer IN $local_ips AND peer <> '0.0.0.0'
    WITH peer,
         sum(CASE WHEN outbound THEN p.SIZE ELSE 0 END) AS upload_bytes,
         sum(CASE WHEN outbound THEN 1 ELSE 0 END) AS upload_packets,
         sum(CASE WHEN NOT outbound THEN p.SIZE ELSE 0 END) AS download_bytes,
         sum(CASE WHEN NOT outbound THEN 1 ELSE 0 END) AS download_packets
    WHERE upload_packets > 0
    OPTIONAL MATCH (pip:IP_ADDRESS {IP_ADDRESS: peer})<-[:OWNERSHIP]-(porg:ORGANIZATION)
    RETURN peer AS ip_address,
           COALESCE(porg.ORGANIZATION, 'Unknown') AS org,
           COALESCE(pip.HOSTNAME, 'Unknown') AS hostname,
           COALESCE(pip.LOCATION, 'Unknown') AS location,
           upload_bytes, upload_packets, download_bytes, download_packets
    ORDER BY upload_bytes DESC
    """
    with driver.session(database=database) as session:
        local_ips = session.run(local_query, {"local_org": LOCAL_ORG}).single()["local_ips"]
        if not local_ips:
            return [], []
        rows = [
            record.data()
            for record in session.run(
                peer_query, {"local_ips": local_ips, "capture_id": capture_id}
            )
        ]
    return local_ips, rows


def add_outlier_to_database(scored_list, flagged_list, driver, database, scope=None):
    # Stamp an explicit OUTLIER verdict on every endpoint that was scored this run:
    # false by default, true for the flagged subset. This makes the property
    # three-state for readers (fetch_traffic / inspect_endpoint): true = flagged,
    # false = scored but clean, absent/null = never scored (anomaly_detection
    # hasn't run for it). Resetting to false first also clears stale true flags
    # from a previous run on the same graph.
    # Scoped to the analyzed profile set: matching on IP alone would overwrite the
    # verdicts stored on that IP's other sessions, rewriting history from one run.
    reset_query = """
    UNWIND $scored AS ip
    MATCH (endpoint:ENDPOINT {IP_ADDRESS: ip})
    WHERE $scope IS NULL OR endpoint.CAPTURE_ID = $scope
    SET endpoint.OUTLIER = false
    """
    flag_query = """
    UNWIND $outliers AS outlier
    MATCH (endpoint:ENDPOINT {IP_ADDRESS: outlier.ip_address})
    WHERE $scope IS NULL OR endpoint.CAPTURE_ID = $scope
    SET endpoint.OUTLIER = true
    """
    with driver.session(database=database) as session:
        session.run(reset_query, {"scored": [e["ip_address"] for e in scored_list], "scope": scope})
        session.run(flag_query, {"outliers": flagged_list, "scope": scope})


def plot_size_over_ports(plot_data, jaws_finder_endpoint):
    _load_plotting()
    plt.figure(num="Packet Size over Ports", figsize=(6, 4))
    for item in plot_data:
        plt.scatter(
            item["size"],
            item["src_port"],
            c=item["size"],
            cmap="winter",
            marker="^",
            s=50,
            alpha=0.1,
            zorder=10,
        )
        plt.scatter(
            item["size"],
            item["dst_port"],
            c=item["size"],
            cmap="ocean",
            marker="^",
            s=50,
            alpha=0.1,
            zorder=10,
        )

    plt.xlabel("SIZE", fontsize=8, color="#666666")
    plt.ylabel("PORT", fontsize=8, color="#666666")
    plt.legend(["SRC_PORT", "DST_PORT"], loc="upper right", fontsize=8)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.grid(True, linewidth=0.5, color="#BEBEBE", alpha=0.5)
    plt.tight_layout()
    save_portsize = os.path.join(jaws_finder_endpoint, "size_over_port.png")
    plt.savefig(save_portsize, dpi=90)

    portsize_plotille = new_plotille_figure()
    portsize_plotille.x_label = "SIZE"
    portsize_plotille.y_label = "PORT"
    portsize_plotille.color_mode = "byte"
    portsize_plotille.width = 80
    portsize_plotille.height = 20
    portsize_plotille.set_x_limits(min_=0)
    portsize_plotille.set_y_limits(min_=0)
    for item in plot_data:
        portsize_plotille.scatter([item["size"]], [item["src_port"]], marker=">")
        portsize_plotille.scatter([item["size"]], [item["dst_port"]], marker="<")
    display_portsize = portsize_plotille.show(legend=False)
    print(display_portsize)


def plot_k_distances(sorted_k_distances, jaws_finder_endpoint):
    _load_plotting()
    plt.figure(num="Sorted K-Distance", figsize=(6, 2))
    plt.plot(
        sorted_k_distances, color="seagreen", marker="o", linestyle="-", linewidth=0.5, alpha=0.8
    )
    plt.grid(color="#BEBEBE", linestyle="-", linewidth=0.25, alpha=0.5)
    plt.xlabel("INDEX", fontsize=8, color="#666666")
    plt.ylabel("K-DISTANCE", fontsize=8, color="#666666")
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_kdistance = os.path.join(jaws_finder_endpoint, "sorted_k_distance.png")
    plt.savefig(save_kdistance, dpi=90)

    kdistance_plotille = new_plotille_figure()
    kdistance_plotille.x_label = "INDEX"
    kdistance_plotille.y_label = "K-DISTANCE"
    kdistance_plotille.color_mode = "byte"
    kdistance_plotille.width = 80
    kdistance_plotille.height = 20
    kdistance_plotille.set_x_limits(min_=0)
    kdistance_plotille.set_y_limits(min_=0)
    plotille_plot_x = list(range(len(sorted_k_distances)))
    kdistance_plotille.plot(plotille_plot_x, sorted_k_distances, marker="o", lc=40)
    display_kdistance = kdistance_plotille.show(legend=False)
    print(display_kdistance)


def recommend_eps(features, min_samples):
    """Knee-recommended DBSCAN eps for a feature space, with a median fallback.

    The same auto-eps procedure main() uses, factored out so the ablation can tune
    each condition's space by an identical rule (a fixed eps is meaningless across
    spaces of different scale/dimensionality).
    """
    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
    nearest_neighbors.fit(features)
    distances, _ = nearest_neighbors.kneighbors(features)
    sorted_k_distances = np.sort(distances[:, min_samples - 1])
    kneedle = KneeLocator(
        range(len(sorted_k_distances)), sorted_k_distances, curve="convex", direction="increasing"
    )
    if kneedle.knee is not None:
        return float(sorted_k_distances[int(kneedle.knee)])
    return float(np.median(sorted_k_distances))


def run_ablation(embeddings, data, components, whiten, feature_weight):
    """Compare three feature-block conditions on the SAME endpoints, no DB writes.

    Answers "how much does the text embedding actually contribute vs the behavioral
    features?" by clustering each condition with an identical procedure (same
    min_samples, per-space knee-recommended eps) and reporting cluster quality
    (silhouette) plus which endpoints each flags (outlier sets + pairwise Jaccard).

      text-only    — the stored embedding alone (feature_weight 0)
      numeric-only — the 12 behavioral features alone, no embedding
      blended      — both, at the requested feature_weight

    Reuses embeddings already on the nodes — nothing is re-embedded.
    """
    text_only, _ = build_feature_matrix(embeddings, data, components, whiten, 0.0)
    numeric_only = StandardScaler().fit_transform(np.log1p(build_numeric_features(data)))
    blended, _ = build_feature_matrix(
        embeddings, data, components, whiten, feature_weight if feature_weight > 0 else 1.0
    )
    conditions = {"text-only": text_only, "numeric-only": numeric_only, "blended": blended}

    min_samples = 2 * components
    ips = [d["ip_address"] for d in data]
    summaries = {}
    outlier_sets = {}
    for name, feats in conditions.items():
        eps = recommend_eps(feats, min_samples)
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(feats)
        clustered = labels != -1
        n_clusters = len(set(labels[clustered]))
        # silhouette needs >=2 clusters and more clustered points than clusters.
        sil = None
        if n_clusters >= 2 and clustered.sum() > n_clusters:
            try:
                sil = float(silhouette_score(feats[clustered], labels[clustered]))
            except ValueError:
                sil = None
        outlier_sets[name] = {ips[i] for i in range(len(ips)) if labels[i] == -1}
        summaries[name] = {
            "dims": int(feats.shape[1]),
            "eps": round(eps, 4),
            "clusters": int(n_clusters),
            "outliers": int((~clustered).sum()),
            "silhouette": round(sil, 4) if sil is not None else None,
        }

    # Pairwise Jaccard of the flagged sets — high overlap means text is decorative
    # (numeric drives the flags); low overlap means the embedding changes outcomes.
    names = list(conditions)
    jaccard = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = outlier_sets[names[i]], outlier_sets[names[j]]
            union = a | b
            jaccard[f"{names[i]} vs {names[j]}"] = (
                round(len(a & b) / len(union), 4) if union else 1.0
            )

    return {
        "endpoints": len(data),
        "min_samples": min_samples,
        "conditions": summaries,
        "outlier_jaccard": jaccard,
        "outlier_sets": {k: sorted(v) for k, v in outlier_sets.items()},
    }


def format_ablation_table(result):
    """Render the ablation result as a fixed-width text table for the reporter."""
    header = (
        f"{'CONDITION':<13}{'DIMS':>5}{'EPS':>9}{'CLUSTERS':>10}{'OUTLIERS':>10}{'SILHOUETTE':>12}"
    )
    rows = [header]
    for name, s in result["conditions"].items():
        sil = "n/a" if s["silhouette"] is None else f"{s['silhouette']:.4f}"
        rows.append(
            f"{name:<13}{s['dims']:>5}{s['eps']:>9.4f}{s['clusters']:>10}{s['outliers']:>10}{sil:>12}"
        )
    rows.append("")
    rows.append("Outlier-set agreement (Jaccard):")
    for pair, jac in result["outlier_jaccard"].items():
        rows.append(f"  {pair:<28}{jac:.4f}")
    return "\n".join(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Perform DBSCAN clustering on embeddings fetched from the database."
    )
    parser.add_argument(
        "--database",
        default=DATABASE,
        help=f"Specify the database to connect to (default: '{DATABASE}').",
    )
    parser.add_argument(
        "--components",
        type=int,
        default=2,
        help="Number of PCA components to retain for clustering. The first 2 are always used for plotting, so values below 2 are clamped (default: 2).",
    )
    parser.add_argument(
        "--whiten",
        action="store_true",
        help="Whiten the PCA components (scale each to unit variance). Improves geometry with few strong components, but amplifies noise when retaining many low-variance components (default: off).",
    )
    parser.add_argument(
        "--eps",
        type=float,
        default=None,
        help="DBSCAN epsilon. When omitted, it is auto-recommended from the k-distance knee. The knee tends to overshoot on small/homogeneous datasets (folding everything into one cluster, 0 outliers) — pass a smaller value to surface more outliers.",
    )
    parser.add_argument(
        "--feature-weight",
        type=float,
        default=1.0,
        help="Influence of the behavioral numeric features (bytes/packets/peers, in & out) on clustering. The numeric block is standardized to unit variance and scaled by this weight; the text embedding keeps its natural scale. 0 = embedding-only (text/org/protocol structure), higher = more volume/fan-out influence to surface behavioral anomalies. Default 1.0.",
    )
    parser.add_argument(
        "--include-local",
        action="store_true",
        help="Include the capture host ('YOU ARE HERE') in the clustered set. Off by default — it is a structural hub that dominates clustering. Its outbound traffic still appears as each remote endpoint's inbound, so outbound anomalies are detectable without it.",
    )
    parser.add_argument(
        "--session",
        default="latest",
        help="Which stored profile set to analyze: 'latest' (default — the most recently computed), or a specific CAPTURE_ID that jaws-compute has profiled. Profile sets accumulate one per capture session.",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Disable historical baselining and score every endpoint purely against its current peers. By default, an endpoint with at least %d prior profiled sessions is measured against its OWN history (volume and shape features only — cadence stays peer-relative so a persistent beacon can't normalize itself), which stops endpoints that are always heavy from dominating the ranking every run."
        % MIN_BASELINE_SESSIONS,
    )
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="Ablation mode: cluster the same endpoints three ways — text-only (embedding alone), numeric-only (behavioral features alone), and blended — and report cluster quality (silhouette) and outlier-set agreement (Jaccard) to quantify how much the embedding contributes. Reuses stored embeddings, writes nothing, generates no plots.",
    )
    args = parser.parse_args()
    reporter = Reporter()
    if args.components < 2:
        args.components = 2
    # Where plots are written. Falls back to a temp dir when JAWS_FINDER_ENDPOINT
    # is unset (e.g. a bare MCP/headless run) so saving never crashes, and the
    # directory is created if missing.
    endpoint = FINDER_ENDPOINT or os.path.join(tempfile.gettempdir(), "jaws")
    os.makedirs(endpoint, exist_ok=True)
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    # Which stored profile set this run analyzes. Profiles accumulate per session, so
    # this has to be pinned before anything reads the ENDPOINT layer.
    try:
        scope, profiled_scopes = resolve_profile_scope(driver, args.database, args.session)
    except ValueError as e:
        reporter.error("ERROR", str(e))
        driver.close()
        return
    if scope is not None:
        reporter.info(
            "CONFIG",
            f"Analyzing profile session: {scope} ({len(profiled_scopes)} profiled session(s) in graph)",
        )

    embeddings, data, excluded_local, excluded_nc = fetch_data_for_dbscan(
        driver, args.database, args.include_local, scope
    )
    if excluded_local:
        reporter.info(
            "CONFIG",
            f"Excluding the local host ('{LOCAL_ORG}') from clustering. Pass --include-local to include it.",
        )
    if excluded_nc:
        reporter.info(
            "CONFIG",
            f"Excluding {len(excluded_nc)} non-conversational endpoint(s) (multicast/broadcast) from clustering and ranking: {', '.join(e['ip_address'] for e in excluded_nc)}",
        )

    # Clustering (and ablation) needs at least min_samples endpoints — below that PCA/
    # NearestNeighbors raise. Catch it here with an actionable message instead.
    min_samples = 2 * args.components
    if len(data) < min_samples:
        if not data:
            reporter.error(
                "ERROR",
                "No embedded endpoints found. Run jaws-capture, jaws-ipinfo, and jaws-compute first.",
            )
        else:
            reporter.error(
                "ERROR",
                f"Clustering needs at least {min_samples} embedded endpoints (have {len(data)}). Capture more traffic or lower --components.",
            )
        driver.close()
        return

    if args.ablate:
        result = run_ablation(embeddings, data, args.components, args.whiten, args.feature_weight)
        reporter.info("ABLATION", format_ablation_table(result))
        reporter.result(
            result,
            summary=f"Ablation over {result['endpoints']} endpoints: text-only vs numeric-only vs blended (no DB writes).",
        )
        driver.close()
        return

    try:
        _load_plotting()
    except ModuleNotFoundError as e:
        reporter.error("ERROR", str(e))
        driver.close()
        return

    plot_data = fetch_data_for_portsize(driver, args.database)
    portsize_info_message = "The below plot shows the packet size over ports.\nIt is useful for identifying ports that are sending or receiving large amounts of data."
    if not reporter.agent:
        reporter.info("INFO", portsize_info_message)
        plot_size_over_ports(plot_data, endpoint)

    feature_info_message = (
        f"Reducing {len(embeddings)} endpoint embeddings to {args.components} PCA dimensions"
        + (
            f", blended with {NUMERIC_FEATURE_COUNT} behavioral features "
            f"(counts, shape ratios & timing; weight {args.feature_weight})."
            if args.feature_weight > 0
            else " (behavioral features disabled)."
        )
    )
    reporter.info("INFO", feature_info_message)

    # Clustering runs on `features` (standardized text PCA components, optionally blended
    # with standardized behavioral features). `plot_xy` is a 2D projection of that same
    # space, so the scatter plot reflects what was actually clustered.
    features, pca = build_feature_matrix(
        embeddings, data, args.components, args.whiten, args.feature_weight
    )
    plot_xy = PCA(n_components=2).fit_transform(features) if features.shape[1] > 2 else features

    explained = pca.explained_variance_ratio_
    per_component = ", ".join(f"PC{i + 1} {v:.2%}" for i, v in enumerate(explained))
    explained_variance_message = (
        f"PCA explained variance ratio: {per_component} "
        f"(total {explained.sum():.2%} of variance retained in {args.components} dimensions)."
    )
    reporter.info("INFO", explained_variance_message)

    kdistance_info_message = (
        "Measuring K-Distance. This is used to determine the optimal epsilon value\nfor DBSCAN."
    )
    reporter.info("INFO", kdistance_info_message)

    nearest_neighbors = NearestNeighbors(n_neighbors=min_samples)
    nearest_neighbors.fit(features)
    distances, _ = nearest_neighbors.kneighbors(features)
    k_distances = distances[:, min_samples - 1]
    sorted_k_distances = np.sort(k_distances)
    if not reporter.agent:
        plot_k_distances(sorted_k_distances, endpoint)

    kneed_info_message = "Using Kneed to recommend EPS.\nKneed is a library that helps us find the knee point in the K-Distance plot."
    reporter.info("INFO", kneed_info_message)

    knee_index = None
    if args.eps is not None:
        # Explicit override — skip the knee recommendation and the interactive prompt.
        eps_value = args.eps
        eps_source = "override"
        reporter.info("CONFIG", f"Using provided EPS: {eps_value}")
    else:
        eps_source = "auto"
        kneedle = KneeLocator(
            range(len(sorted_k_distances)),
            sorted_k_distances,
            curve="convex",
            direction="increasing",
        )
        knee_index = int(kneedle.knee) if kneedle.knee is not None else None
        if knee_index is not None:
            eps_value = sorted_k_distances[knee_index]
            reporter.info("INFO", f"Knee point found at index: {knee_index}")
        else:
            reporter.info("INFO", "Knee point not found. Using default EPS.")
            eps_value = np.median(sorted_k_distances)

        if not reporter.agent:
            user_input = input(
                f"[RECOMMENDED EPS] {eps_value:.2f} | Press ENTER to accept, or provide a value: "
            )
            if user_input:
                try:
                    eps_value = float(user_input)
                    eps_source = "manual"
                except ValueError:
                    reporter.error("ERROR", "Invalid input. Using the recommended EPS value.")
            reporter.info("INFO", "Matplotlib plots will be generated after passing an EPS value.")
        else:
            reporter.info("CONFIG", "Skipping user input and passing the recommended EPS value.")

    dbscan = DBSCAN(eps=eps_value, min_samples=min_samples)
    clusters = dbscan.fit_predict(features)
    # Cluster count and sizes distinguish "one tight benign cluster" from "a generous
    # eps absorbed everything" when reading outliers_flagged == 0.
    cluster_sizes = sorted(
        (int(n) for n in np.unique(clusters[clusters != -1], return_counts=True)[1]), reverse=True
    )

    if not reporter.agent:
        reporter.info(
            "INFO",
            "The below plot shows the PCA/DBSCAN outliers, in red, from the embeddings.\nAdditionally, embedding clusters are shown to help understand how outliers are distributed amongst noise.",
        )

    plt.figure(
        num=f"PCA/DBSCAN Outliers from Embeddings | n_components: {args.components}, min_samples: {min_samples}, eps: {eps_value}",
        figsize=(8, 7),
    )
    clustered_indices = clusters != -1
    plt.scatter(
        plot_xy[clustered_indices, 0],
        plot_xy[clustered_indices, 1],
        c=clusters[clustered_indices],
        cmap="winter",
        edgecolors="none",
        marker="^",
        s=50,
        alpha=0.1,
        zorder=2,
    )

    outlier_indices = clusters == -1
    plt.scatter(
        plot_xy[outlier_indices, 0],
        plot_xy[outlier_indices, 1],
        color="red",
        marker="o",
        s=50,
        label="Outliers",
        alpha=0.8,
        zorder=10,
    )

    for i, item in enumerate(data):
        annotation_text = f"{item['ip_address']}\n{item['org']}\n{item['hostname']}\n{item['location']}\nout {item['bytes_out']}B/{item['packets_out']}p | in {item['bytes_in']}B/{item['packets_in']}p"
        if clusters[i] == -1:
            # Outlier
            bbox_style = dict(
                boxstyle="round,pad=0.2", facecolor="#333333", edgecolor="none", alpha=0.9
            )
            plt.annotate(
                annotation_text,
                (plot_xy[i, 0], plot_xy[i, 1]),
                fontsize=6,
                color="white",
                bbox=bbox_style,
                horizontalalignment="center",
                verticalalignment="bottom",
                xytext=(0, 10),
                textcoords="offset points",
                alpha=0.9,
                zorder=10,
            )
        else:
            # Non-Outlier
            bbox_style = dict(
                boxstyle="round,pad=0.2", facecolor="#BEBEBE", edgecolor="none", alpha=0.5
            )
            plt.annotate(
                annotation_text,
                (plot_xy[i, 0], plot_xy[i, 1]),
                fontsize=6,
                color="#666666",
                bbox=bbox_style,
                horizontalalignment="center",
                verticalalignment="bottom",
                xytext=(0, 10),
                textcoords="offset points",
                alpha=0.8,
                zorder=1,
            )

    plt.grid(color="#BEBEBE", linestyle="-", linewidth=0.25, alpha=0.5)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    save_outliers = os.path.join(endpoint, "pca_dbscan_outliers.png")
    plt.savefig(save_outliers, dpi=90)

    outlier_plotille = new_plotille_figure()
    outlier_plotille.color_mode = "byte"
    outlier_plotille.width = 80
    outlier_plotille.height = 20
    clustered_indices_pc1 = plot_xy[clustered_indices, 0]
    clustered_indices_pc2 = plot_xy[clustered_indices, 1]
    outlier_indices_pc1 = plot_xy[outlier_indices, 0]
    outlier_indices_pc2 = plot_xy[outlier_indices, 1]
    outlier_plotille.scatter(clustered_indices_pc1, clustered_indices_pc2, marker="^")
    outlier_plotille.scatter(outlier_indices_pc1, outlier_indices_pc2, marker="o")
    display_outlier = outlier_plotille.show(legend=False)

    if not reporter.agent:
        reporter.raw(display_outlier)

    # Every endpoint gets a rankable anomaly score and reason codes, sorted most
    # anomalous first; `is_outlier` marks the DBSCAN-flagged ones. Returning the full
    # ranked list (not just the flagged subset) means there is always something to
    # triage — a 0-outlier DBSCAN run still yields a ranking.
    # Per-endpoint history from the profile sets preceding this one, so the score asks
    # "unusual for THIS endpoint" where it can and falls back to "unusual for the pack"
    # where it can't. Skipped for the pooled 'all' scope, which overlaps every session
    # and so has no coherent "before".
    # Profile sets that PRECEDE the analyzed one — the same set _HISTORY_QUERY draws on,
    # so the reported depth matches what was actually available to baseline against. The
    # pooled scope has no position in the sequence, so nothing is prior to it.
    baselineable = scope is not None and scope != POOLED_SCOPE and not args.no_baseline
    prior_scopes = (
        [s for s in profiled_scopes if s not in (scope, POOLED_SCOPE, None) and s < scope]
        if baselineable
        else []
    )
    history = {}
    if args.no_baseline:
        skipped_because = "disabled with --no-baseline"
        reporter.info(
            "CONFIG",
            "Historical baselining disabled (--no-baseline): scoring against current peers only.",
        )
    elif scope == POOLED_SCOPE:
        skipped_because = (
            f"the pooled '{POOLED_SCOPE}' scope re-aggregates every session at once, so it "
            "overlaps all of them and has no prior session to compare against"
        )
        reporter.info(
            "CONFIG",
            f"Historical baselining skipped: the pooled '{POOLED_SCOPE}' scope re-aggregates every session, so it has no prior sessions to baseline against.",
        )
    elif scope is None:
        skipped_because = (
            "these profiles predate capture-session stamping, so they carry no session to order by"
        )
    else:
        skipped_because = None
        history = fetch_endpoint_history(driver, args.database, scope)

    ranked_endpoints = score_endpoints(data, clusters, history)
    flagged = [e for e in ranked_endpoints if e["is_outlier"]]
    baselined_endpoints = [
        e for e in ranked_endpoints if e["baseline_sessions"] >= MIN_BASELINE_SESSIONS
    ]
    new_endpoints = [e for e in ranked_endpoints if e["first_seen"]]
    if history:
        reporter.info(
            "BASELINE",
            f"{len(baselined_endpoints)} of {len(ranked_endpoints)} endpoint(s) scored against their own history "
            f"(>= {MIN_BASELINE_SESSIONS} prior sessions); {len(new_endpoints)} never seen in a prior session.",
        )

    add_outlier_to_database(ranked_endpoints, flagged, driver, args.database, scope)

    # First-class host-outbound view: outbound FROM the capture host, per destination,
    # isolated from raw packets (host as source). The remote-endpoint ranking above is
    # dominated by inbound/download volume and structurally demotes the host's own outbound
    # (the documented purpose) — this re-centers on it, independent of clustering and the
    # --include-local flag. Empty when the host wasn't captured (e.g. an imported pcap).
    # The host-outbound packet scan is scoped to the same session the analyzed profile
    # set describes, so both views cover the same traffic. The pooled 'all' scope (and a
    # legacy graph's unstamped profiles) means scan everything.
    packet_scope = None if scope == POOLED_SCOPE else scope
    local_ips, host_rows = fetch_host_outbound(driver, args.database, packet_scope)
    # Multicast/broadcast "destinations" (SSDP/mDNS announcements) never reply, so
    # their upload_download_ratio is structurally huge — drop them before ranking
    # rather than let protocol chatter read as exfil-shaped.
    conversational_rows = [
        r for r in host_rows if classify_endpoint(r["ip_address"]) not in NON_CONVERSATIONAL_TYPES
    ]
    host_excluded_nc = len(host_rows) - len(conversational_rows)
    host_destinations = score_host_outbound(conversational_rows)
    host_flagged = [d for d in host_destinations if d["is_flagged"]]
    if not reporter.agent and host_destinations:
        top = host_destinations[0]
        reporter.info(
            "HOST OUTBOUND",
            f"Top outbound destination from the host: {top['ip_address']} ({top['org']}) "
            f"— {top['upload_bytes']} bytes up / {top['download_bytes']} down "
            f"(score {top['outbound_score']}). {len(host_flagged)} destination(s) flagged.",
        )

    # Structured result with the clustering diagnostics folded in (so the caller gets
    # the useful numbers as fields, not prose). `endpoints` is the full ranked list;
    # an empty `flagged` set (outliers_flagged == 0) means DBSCAN flagged nothing —
    # not a failure — but anomaly_score still ranks every endpoint. `units` labels the
    # raw magnitudes; `reason_z_threshold` is the robust-z cutoff for citing a feature.
    result = {
        "endpoints_clustered": len(data),
        "outliers_flagged": len(flagged),
        "clusters": len(cluster_sizes),
        "cluster_sizes": cluster_sizes,
        # Capture session the ENDPOINT profiles (and the host_outbound scan) describe:
        # a CAPTURE_ID, or 'all' when profiles span every session / predate sessions.
        "session": scope or POOLED_SCOPE,
        "profiled_sessions": profiled_scopes,
        # How much of this ranking is historical vs. purely peer-relative.
        # `endpoints_baselined` counts endpoints measured against their own past;
        # `first_seen` names the IPs that have never appeared in a prior session — a
        # finding on its own, since no per-feature deviation can express "brand new".
        # `enabled` reports whether baselining actually took EFFECT, not merely whether
        # history was fetched: prior sessions can exist while no single endpoint appears
        # in enough of them, and that run is still entirely peer-relative.
        "baseline": {
            "enabled": bool(baselined_endpoints),
            "min_sessions": MIN_BASELINE_SESSIONS,
            "history_sessions": len(prior_scopes),
            "endpoints_baselined": len(baselined_endpoints),
            "first_seen": [e["ip_address"] for e in new_endpoints],
            "exempt_features": sorted(BASELINE_EXEMPT_FEATURES),
            "description": (
                "Endpoints with at least min_sessions prior profiled sessions are scored against "
                "their OWN historical median instead of the current population median, so an "
                "endpoint that is always heavy stops ranking on volume it posts every session and "
                "a change in its behavior ranks instead. Each reason names its reference via "
                "`compared_to` ('own history' or 'peer endpoints') and carries the `baseline` it "
                "was measured against. exempt_features are never baselined — a beacon's regularity "
                "is identical in every session, so its own history would declare it normal; cadence "
                "stays peer-relative. Population-wide shifts (e.g. a longer capture) cancel out."
                if baselined_endpoints
                else f"Not applied — every score here is peer-relative — because {skipped_because}."
                if skipped_because
                else f"Not applied — every score here is peer-relative. {len(prior_scopes)} profiled "
                f"session(s) precede this one, and no endpoint appeared in the {MIN_BASELINE_SESSIONS} "
                "required to form a baseline. Run more capture -> compute -> detect cycles: once the "
                "same IPs recur, they are scored against their own history instead."
            ),
        },
        "excluded_local": excluded_local,
        "eps": round(float(eps_value), 4),
        "eps_source": eps_source,
        "knee_index": knee_index,
        "min_samples": min_samples,
        "components": args.components,
        "feature_weight": args.feature_weight,
        "pca_variance": [round(float(v), 4) for v in explained],
        "pca_variance_total": round(float(explained.sum()), 4),
        "units": FEATURE_UNITS,
        "reason_z_threshold": REASON_Z_THRESHOLD,
        "scoring": (
            f"anomaly_score = L2 norm of the {SCORE_TOP_K} largest |robust-z| components. 'Low' "
            f"deviations (quieter than the pack) are weighted {LOW_DIRECTION_WEIGHT} and capped at "
            f"{LOW_DIRECTION_CAP} each, so a silent endpoint can never outrank a genuine spike — "
            "except interval_cv, where low = beacon-regular and keeps full weight. interval_mean "
            f"is capped at {LOW_DIRECTION_CAP} in BOTH directions: cadence is context, not signal "
            "(interval_cv carries the beacon indicator). Timing is measured on the endpoint's more "
            "regular single direction (a combined stream's request/response pairing forces CV "
            "toward 1.0 and hides real beacons); it contributes 0 to the score for endpoints with "
            f"fewer than {MIN_TIMING_PACKETS} packets in each direction (their interval fields "
            "read null). Reasons cite unweighted robust-z in both directions."
        ),
        # Multicast/broadcast/unspecified addresses are one-way by construction (no
        # replies), so their out/in shape is protocol chatter, not behavior — they are
        # profiled in the graph (see endpoint_type) but not clustered or ranked.
        "excluded_non_conversational": {
            "count": len(excluded_nc),
            "endpoints": excluded_nc,
        },
        # Counts are from each endpoint's OWN perspective. With the capture host excluded
        # (the default), endpoints are remote, so a remote IP's bytes_out/packets_out is
        # traffic it sent TO the host (a host download) and bytes_in/packets_in is traffic
        # the host sent to it (the outbound-from-host signal). Each directional reason
        # carries a `host_relative` gloss; don't read a high bytes_out on a remote IP as exfil.
        "perspective": (
            "Endpoint counts are from the endpoint's own perspective. Scored endpoints are "
            "remote (capture host excluded by default): bytes_out/packets_out = traffic the "
            "remote IP sent to the host (host download); bytes_in/packets_in = traffic the host "
            "sent to it (outbound from host). See each reason's host_relative field. The "
            "host_outbound section re-frames the same capture from the host's perspective."
        ),
        "endpoints": ranked_endpoints,
        # The defender-frame counterpart to `endpoints`: the capture host's OWN outbound,
        # per destination, ranked on the host-upload distribution. Directly serves the
        # tool's purpose ("unusual outbound from the local host"), which the remote-endpoint
        # ranking demotes. `local_ips` is empty when no local host was captured.
        "host_outbound": {
            "local_ips": local_ips,
            "destinations_ranked": len(host_destinations),
            "flagged": len(host_flagged),
            "excluded_non_conversational": host_excluded_nc,
            "units": HOST_OUTBOUND_UNITS,
            "note": (
                "Outbound FROM the capture host, per destination, isolated from raw packets "
                "(host as source, so upload_bytes is host→peer, not the peer's total inbound). "
                "`outbound_score` ranks destinations on the host-upload distribution; "
                "upload_download_ratio high = exfil-shaped (host sent far more than it received). "
                "Rows with `is_flagged` true are the `flagged` count. "
                "Use this, not the download-dominated `endpoints` ranking, to judge host exfil/beaconing."
            ),
            "destinations": host_destinations,
        },
    }

    if not reporter.agent:
        plt.show()

    reporter.result(
        result,
        summary=f"Clustered {len(data)} endpoints (per IP); {len(flagged)} outlier(s) flagged, all ranked by anomaly_score; {len(host_flagged)} host-outbound destination(s) flagged. Plots saved to: {endpoint}",
    )

    driver.close()


if __name__ == "__main__":
    main()
