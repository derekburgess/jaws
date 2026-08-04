"""Per-endpoint historical baselining in jaws_finder.

These pin the design decisions baselining depends on. Several are reversible by an
innocent-looking refactor and would fail silently in production, so they assert the
invariant directly rather than the output that happens to follow from it:

  - baselined and peer-relative rows are standardized in SEPARATE frames
  - BASELINE_SCALE_FLOOR keeps magnitude meaningful when a population is degenerate
  - cadence (interval_mean/interval_cv) is NEVER baselined
"""

import numpy as np
import pytest
from conftest import (
    ep,
    hist,
    history_from,
    no_clusters,
    rank_of,
    row_of,
    score_of,
)

from jaws.jaws_finder import (
    BASELINE_EXEMPT_FEATURES,
    MIN_BASELINE_SESSIONS,
    NUMERIC_FEATURE_NAMES,
    baselined_z_scores,
    build_baseline_centers,
    build_numeric_features,
    robust_z_scores,
    score_endpoints,
)

# --------------------------------------------------------------- 1. regression
# With no history the score must be bit-identical to the pre-baseline behavior.


def test_no_history_leaves_scoring_unchanged(pack):
    raw = build_numeric_features(pack)
    z_old = robust_z_scores(raw)
    centers, sessions = build_baseline_centers(pack, {}, NUMERIC_FEATURE_NAMES, raw)

    assert not sessions.any()
    assert np.allclose(centers, np.tile(np.median(np.log1p(raw), axis=0), (len(pack), 1)))
    assert np.array_equal(
        baselined_z_scores(raw, centers, sessions > 0, NUMERIC_FEATURE_NAMES), z_old
    )


def test_none_and_empty_history_agree(pack):
    a = score_endpoints(pack, no_clusters(len(pack)), None)
    b = score_endpoints(pack, no_clusters(len(pack)), {})
    assert [e["anomaly_score"] for e in a] == [e["anomaly_score"] for e in b]


def test_separate_frames_leave_unbaselined_rows_untouched(pack):
    """The two-frame split. Pooling them lets a stable population drag the shared
    MAD toward zero, which is what test_scale_floor_* guards from the other side."""
    raw = build_numeric_features(pack)
    z_old = robust_z_scores(raw)
    centers, _ = build_baseline_centers(pack, {}, NUMERIC_FEATURE_NAMES, raw)

    mixed = np.zeros(len(pack), dtype=bool)
    mixed[:4] = True
    z_mixed = baselined_z_scores(raw, centers, mixed, NUMERIC_FEATURE_NAMES)

    assert np.allclose(z_mixed[~mixed], z_old[~mixed])


# ----------------------------------------------- 2/3. demotion and genuine change


def test_persistent_heavy_endpoint_is_demoted_by_its_own_history(pack, heavy, heavy_history):
    current = pack + [heavy]
    peer = score_endpoints(current, no_clusters(len(current)), None)
    based = score_endpoints(current, no_clusters(len(current)), heavy_history)

    assert rank_of(peer, "10.0.0.99") == 0, "should top the peer-relative ranking"
    assert score_of(based, "10.0.0.99") < score_of(peer, "10.0.0.99")


def test_departure_from_own_history_ranks_first(pack, heavy_history):
    changed = ep("10.0.0.99", 10_000_000, 4000, 8000, 40)
    current = pack + [changed]
    ranked = score_endpoints(current, no_clusters(len(current)), heavy_history)
    top = ranked[0]

    assert top["ip_address"] == "10.0.0.99"
    own = [r for r in top["reasons"] if r["compared_to"] == "own history"]
    assert own, "a departure must be explained against its own history"
    assert all("baseline" in r for r in own)
    assert all(r.get("baseline_sessions") == 3 for r in own)


def test_departure_outscores_steady_state(pack, heavy, heavy_history):
    steady = score_endpoints(pack + [heavy], no_clusters(len(pack) + 1), heavy_history)
    changed = ep("10.0.0.99", 10_000_000, 4000, 8000, 40)
    moved = score_endpoints(pack + [changed], no_clusters(len(pack) + 1), heavy_history)

    assert score_of(moved, "10.0.0.99") > score_of(steady, "10.0.0.99")


# ------------------------------------------- 4. population-wide shifts cancel out


def test_uniform_population_shift_raises_no_reasons(pack, pack_history):
    """Every endpoint 4x busier — a 120s capture after 30s ones. Nothing should light up."""
    scaled = [
        ep(
            p["ip_address"],
            p["bytes_out"] * 4,
            p["bytes_in"] * 4,
            p["packets_out"] * 4,
            p["packets_in"] * 4,
        )
        for p in pack
    ]
    ranked = score_endpoints(scaled, no_clusters(len(scaled)), pack_history)

    assert sum(len(e["reasons"]) for e in ranked) == 0


def test_endpoint_exceeding_the_population_shift_still_surfaces(pack, pack_history):
    scaled = [
        ep(
            p["ip_address"],
            p["bytes_out"] * 4,
            p["bytes_in"] * 4,
            p["packets_out"] * 4,
            p["packets_in"] * 4,
        )
        for p in pack[:-1]
    ]
    last = pack[-1]
    scaled.append(
        ep(
            last["ip_address"],
            last["bytes_out"] * 64,
            last["bytes_in"] * 4,
            last["packets_out"] * 64,
            last["packets_in"] * 4,
        )
    )
    ranked = score_endpoints(scaled, no_clusters(len(scaled)), pack_history)

    assert ranked[0]["ip_address"] == last["ip_address"]
    assert ranked[0]["reasons"]


# ------------------------------------------------- 5. cadence is NEVER baselined


@pytest.fixture
def beacon_population():
    pack = [
        ep(f"10.1.0.{i}", 5000, 6000, 30, 30, im=0.4 + i * 0.05, icv=0.9 + i * 0.02)
        for i in range(12)
    ]
    beacon = ep("10.1.0.99", 5000, 6000, 30, 30, im=60.0, icv=0.001)
    return pack + [beacon]


def test_persistent_beacon_stays_flagged_despite_identical_history(beacon_population):
    """A beacon looks the same every session, so baselining its cadence would let its
    own history declare it normal — silencing the strongest signal the tool has."""
    history = history_from([beacon_population for _ in range(3)])
    ranked = score_endpoints(beacon_population, no_clusters(len(beacon_population)), history)
    row = row_of(ranked, "10.1.0.99")

    cv = next((r for r in row["reasons"] if r["feature"] == "interval_cv"), None)
    assert cv is not None, f"reasons={[r['feature'] for r in row['reasons']]}"
    assert cv["direction"] == "low"
    assert cv["compared_to"] == "peer endpoints"


def test_timing_features_are_declared_exempt():
    assert BASELINE_EXEMPT_FEATURES == {"interval_mean", "interval_cv"}


def test_exempt_feature_centers_stay_at_the_column_median(beacon_population):
    history = history_from([beacon_population for _ in range(3)])
    raw = build_numeric_features(beacon_population)
    centers, _ = build_baseline_centers(beacon_population, history, NUMERIC_FEATURE_NAMES, raw)
    column_median = np.median(np.log1p(raw), axis=0)

    for feature in BASELINE_EXEMPT_FEATURES:
        j = NUMERIC_FEATURE_NAMES.index(feature)
        assert np.allclose(centers[:, j], column_median[j]), feature


# ------------------------------------------------------ 6. first_seen / novelty


def test_first_seen_marks_novel_endpoints(pack, pack_history):
    brand_new = ep("10.0.0.250", 3000, 3000, 25, 25)
    current = pack + [brand_new]
    ranked = score_endpoints(current, no_clusters(len(current)), pack_history)

    new_row = row_of(ranked, "10.0.0.250")
    returning = row_of(ranked, "10.0.0.0")

    assert new_row["first_seen"] is True
    assert new_row["baseline_sessions"] == 0
    assert returning["first_seen"] is False
    assert returning["baseline_sessions"] == 3
    assert all(r["compared_to"] == "peer endpoints" for r in new_row["reasons"])


def test_first_seen_is_none_without_any_history(pack):
    ranked = score_endpoints(pack, no_clusters(len(pack)), {})
    assert ranked[0]["first_seen"] is None


# --------------------------------------- 7. insufficient history -> peer-relative


def test_history_below_min_sessions_is_not_used(pack, heavy):
    current = pack + [heavy]
    raw = build_numeric_features(current)
    thin = {"10.0.0.99": hist(MIN_BASELINE_SESSIONS - 1, bytes_out=490_000)}
    centers, sessions = build_baseline_centers(current, thin, NUMERIC_FEATURE_NAMES, raw)

    assert not sessions.any()
    assert np.allclose(centers, np.tile(np.median(np.log1p(raw), axis=0), (len(current), 1)))

    peer = score_endpoints(current, no_clusters(len(current)), None)
    ranked_thin = score_endpoints(current, no_clusters(len(current)), thin)
    assert [e["ip_address"] for e in ranked_thin] == [e["ip_address"] for e in peer]


# ----------------------------------------- 8. missing / degenerate baseline input


def test_none_and_nan_baselines_fall_back_to_the_column_median(pack, heavy):
    current = pack + [heavy]
    medians = {n: 1.0 for n in NUMERIC_FEATURE_NAMES if n not in ("bytes_out", "packets_out")}
    medians.update(bytes_out=None, packets_out=float("nan"))
    partial = {"10.0.0.99": {"sessions": 3, "medians": medians}}

    centers, _ = build_baseline_centers(
        current, partial, NUMERIC_FEATURE_NAMES, build_numeric_features(current)
    )
    assert np.all(np.isfinite(centers))


def test_all_zero_baseline_is_finite(pack, heavy):
    current = pack + [heavy]
    centers, _ = build_baseline_centers(
        current, {"10.0.0.99": hist(3)}, NUMERIC_FEATURE_NAMES, build_numeric_features(current)
    )
    assert np.all(np.isfinite(centers))


def test_single_endpoint_set_does_not_crash(pack, pack_history):
    assert len(score_endpoints([pack[0]], no_clusters(1), pack_history)) == 1


# ------------------------------- 9. the scale floor keeps magnitude meaningful
# Every endpoint reproduces its history exactly, so the residual MAD collapses to 0.
# Without BASELINE_SCALE_FLOOR the one changed endpoint sets the scale itself and its
# z goes magnitude-invariant — a 20x change scoring the same as a 1% one.


@pytest.fixture
def degenerate(pack):
    stable = history_from([pack, pack, pack])
    last = pack[-1]
    small = pack[:-1] + [
        ep(
            last["ip_address"],
            int(last["bytes_out"] * 1.01),
            last["bytes_in"],
            last["packets_out"],
            last["packets_in"],
        )
    ]
    big = pack[:-1] + [
        ep(
            last["ip_address"],
            last["bytes_out"] * 20,
            last["bytes_in"],
            last["packets_out"],
            last["packets_in"],
        )
    ]
    return stable, small, big, last["ip_address"]


def test_scale_floor_keeps_a_20x_change_above_a_1pct_one(degenerate):
    stable, small, big, ip = degenerate
    s_small = score_endpoints(small, no_clusters(len(small)), stable)
    s_big = score_endpoints(big, no_clusters(len(big)), stable)

    assert score_of(s_big, ip) > score_of(s_small, ip) * 5


def test_scale_floor_leaves_a_1pct_change_unremarkable(degenerate):
    stable, small, _, _ = degenerate
    ranked = score_endpoints(small, no_clusters(len(small)), stable)
    assert not any(e["reasons"] for e in ranked)


def test_scale_floor_keeps_scores_bounded(degenerate):
    stable, _, big, ip = degenerate
    score = score_of(score_endpoints(big, no_clusters(len(big)), stable), ip)
    assert np.isfinite(score) and score < 1e4
