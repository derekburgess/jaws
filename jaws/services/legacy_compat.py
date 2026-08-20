"""Legacy finder response adapter over Milestone 4 typed services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from importlib import import_module
from typing import Any, cast

import numpy as np

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    HOST_DESTINATION_NUMERIC_FEATURE_SET_V1,
    ComparisonFrame,
    EntityId,
    RankerSpec,
    ReferenceEligibility,
    ReferenceEntity,
    ReferenceResult,
    RepresentationArtifacts,
)

from .explanations import ExplanationService
from .feature_registry import (
    ENDPOINT_FEATURE_REGISTRY_V1,
    HOST_DESTINATION_FEATURE_REGISTRY_V1,
)
from .legacy_ranking import (
    KDistanceEpsilonStrategy,
    LegacyBehavioralRanker,
    LegacyRankerParameters,
    deviation_scores,
    robust_center_scale,
    weighted_deviations,
)

NUMERIC_FEATURE_SET = ENDPOINT_NUMERIC_FEATURE_SET_V1
NUMERIC_FEATURE_NAMES = list(NUMERIC_FEATURE_SET.feature_names)
NUMERIC_FEATURE_COUNT = len(NUMERIC_FEATURE_NAMES)
BASE_FEATURES = [row.name for row in NUMERIC_FEATURE_SET.features if row.family.value == "base"]
TIMING_FEATURES = [row.name for row in NUMERIC_FEATURE_SET.features if row.family.value == "timing"]
FEATURE_UNITS = {row.name: row.unit.value for row in NUMERIC_FEATURE_SET.features}
REASON_Z_THRESHOLD = 2.5
SCORE_TOP_K = 3
LOW_DIRECTION_WEIGHT = 0.5
LOW_DIRECTION_CAP = 3.0
LOW_SIGNAL_FEATURES = {"interval_cv"}
SATURATING_FEATURES = {"interval_mean"}
MIN_BASELINE_SESSIONS = 2
BASELINE_EXEMPT_FEATURES = set(TIMING_FEATURES)
BASELINE_SCALE_FLOOR = 0.1
LOCAL_ORG = "YOU ARE HERE"

HOST_OUTBOUND_FEATURES = ["upload_bytes", "upload_packets", "upload_download_ratio"]
HOST_OUTBOUND_UNITS = {
    row.name: row.unit.value
    for row in HOST_DESTINATION_NUMERIC_FEATURE_SET_V1.features
    if row.name in HOST_OUTBOUND_FEATURES
}
HOST_OUTBOUND_GLOSS = {
    row.name: cast(str, row.host_flow.local_interpretation)
    for row in HOST_DESTINATION_FEATURE_REGISTRY_V1.metadata
    if row.name in HOST_OUTBOUND_FEATURES and row.host_flow is not None
}


def _cloud_hosted(organization: object) -> bool:
    function = cast(Any, getattr(import_module("jaws.config"), "is_cloud_hosted"))
    return bool(function(organization))


def host_relative_gloss(feature: str, is_local: bool) -> str | None:
    return ENDPOINT_FEATURE_REGISTRY_V1.host_relative_gloss(feature, is_local=is_local)


def build_numeric_features(data: Sequence[Mapping[str, object]]) -> np.ndarray:
    return ENDPOINT_FEATURE_REGISTRY_V1.raw_matrix(data)


def _transform_numeric_feature(value: float, feature: object) -> float:
    del feature
    return float(np.log1p(value))


def transform_numeric_features(raw: np.ndarray) -> np.ndarray:
    return ENDPOINT_FEATURE_REGISTRY_V1.transform(raw)


def _robust_center_scale(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return robust_center_scale(matrix)


def robust_z_scores(raw: np.ndarray, *, feature_set: object = NUMERIC_FEATURE_SET) -> np.ndarray:
    transformed = np.log1p(raw) if feature_set is None else transform_numeric_features(raw)
    center, scale = robust_center_scale(transformed)
    z = np.zeros_like(transformed)
    usable = scale > 1e-9
    z[:, usable] = (transformed[:, usable] - center[usable]) / scale[usable]
    return z


def deviation_score(z: np.ndarray, feature_names: Sequence[str]) -> np.ndarray:
    parameters = LegacyRankerParameters()
    return deviation_scores(weighted_deviations(z, feature_names, parameters), parameters.top_k)


def build_baseline_centers(
    data: Sequence[Mapping[str, object]],
    history: Mapping[str, Mapping[str, Any]],
    feature_names: Sequence[str],
    raw: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    transformed = transform_numeric_features(raw)
    centers = np.tile(np.median(transformed, axis=0), (len(data), 1))
    sessions = np.zeros(len(data), dtype=int)
    for index, row in enumerate(data):
        entry = history.get(str(row["ip_address"]))
        if entry is None or int(entry["sessions"]) < MIN_BASELINE_SESSIONS:
            continue
        sessions[index] = int(entry["sessions"])
        medians = cast(Mapping[str, object], entry["medians"])
        for column, name in enumerate(feature_names):
            if name in BASELINE_EXEMPT_FEATURES:
                continue
            value = medians.get(name)
            numeric = cast(float | int | str | None, value)
            if numeric is not None and np.isfinite(float(numeric)):
                centers[index, column] = np.log1p(max(float(numeric), 0.0))
    return centers, sessions


def baselined_z_scores(
    raw: np.ndarray, centers: np.ndarray, baselined: np.ndarray, feature_names: Sequence[str]
) -> np.ndarray:
    z = robust_z_scores(raw)
    if not baselined.any():
        return z
    columns = [
        index for index, name in enumerate(feature_names) if name not in BASELINE_EXEMPT_FEATURES
    ]
    if not columns:
        return z
    residual = transform_numeric_features(raw) - centers
    center, scale = robust_center_scale(residual[baselined])
    scale = np.maximum(scale, BASELINE_SCALE_FLOOR)
    history_z = (residual - center) / scale
    z[np.ix_(baselined, columns)] = history_z[np.ix_(baselined, columns)]
    return z


def _representation(
    entities: tuple[EntityId, ...], raw: np.ndarray, transformed: np.ndarray, *, host: bool = False
) -> RepresentationArtifacts:
    feature_set = (
        HOST_DESTINATION_NUMERIC_FEATURE_SET_V1 if host else ENDPOINT_NUMERIC_FEATURE_SET_V1
    )
    return RepresentationArtifacts(
        feature_set.feature_set_id,
        feature_set.version,
        entities,
        feature_set.feature_names,
        tuple(tuple(float(value) for value in row) for row in raw),
        tuple(tuple(float(value) for value in row) for row in transformed),
        tuple(tuple(float(value) for value in row) for row in transformed),
    )


def score_endpoints(
    data: Sequence[Mapping[str, Any]],
    clusters: Sequence[int],
    history: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows = tuple(data)
    raw = build_numeric_features(rows)
    transformed = transform_numeric_features(raw)
    centers, sessions = build_baseline_centers(rows, history or {}, NUMERIC_FEATURE_NAMES, raw)
    entities = tuple(EntityId(f"ip:{row['ip_address']}") for row in rows)
    frames = np.empty(raw.shape, dtype=object)
    frames[:] = ComparisonFrame.PEER
    for index, depth in enumerate(sessions):
        if depth >= MIN_BASELINE_SESSIONS:
            for column, name in enumerate(NUMERIC_FEATURE_NAMES):
                if name not in BASELINE_EXEMPT_FEATURES:
                    frames[index, column] = ComparisonFrame.OWN_HISTORY
    any_history = bool(history)
    eligibility = []
    for index, entity in enumerate(entities):
        if sessions[index] >= MIN_BASELINE_SESSIONS:
            status = ReferenceEligibility.HISTORICAL
        elif any_history and str(rows[index]["ip_address"]) not in cast(Mapping[str, Any], history):
            status = ReferenceEligibility.FIRST_SEEN
        elif any_history:
            status = ReferenceEligibility.INSUFFICIENT_HISTORY
        else:
            status = ReferenceEligibility.PEER
        eligibility.append(
            ReferenceEntity(
                entity,
                status,
                int(sessions[index]),
                None if not any_history else status is ReferenceEligibility.FIRST_SEEN,
            )
        )
    reference = ReferenceResult(
        "hybrid" if any_history else "peer",
        "1",
        entities,
        tuple(NUMERIC_FEATURE_NAMES),
        tuple(tuple(float(value) for value in row) for row in centers),
        tuple(tuple(value for value in row) for row in frames),
        tuple(eligibility),
        entities,
    )
    representation = _representation(entities, raw, transformed)
    result = LegacyBehavioralRanker(ENDPOINT_FEATURE_REGISTRY_V1).rank(
        entities,
        representation,
        reference,
        RankerSpec(ranker_id="legacy_2_0", version="1", seed=0),
        labels=clusters,
    )
    by_entity = dict(zip(entities, rows, strict=True))
    explanations = ExplanationService(ENDPOINT_FEATURE_REGISTRY_V1)
    ranked = []
    for finding in result.findings:
        item = by_entity[finding.entity_id]
        is_local = item.get("org") == LOCAL_ORG
        explanation = explanations.explain(
            finding, is_local=is_local, cloud_hosted=_cloud_hosted(item.get("org"))
        )
        reasons = []
        for reason in explanation.reasons:
            payload: dict[str, Any] = {
                "feature": reason.feature,
                "value": round(reason.value, 4),
                "unit": reason.unit,
                "robust_z": round(reason.standardized_deviation, 2),
                "direction": reason.direction,
                "compared_to": reason.comparison_frame,
            }
            if reason.baseline is not None:
                payload["baseline"] = round(reason.baseline, 4)
                payload["baseline_sessions"] = reason.baseline_depth
            if reason.host_relative:
                payload["host_relative"] = reason.host_relative
            reasons.append(payload)
        ranked.append(
            {
                "ip_address": item["ip_address"],
                "endpoint_type": item.get("endpoint_type"),
                "org": item["org"],
                "cloud_hosted": _cloud_hosted(item.get("org")),
                "hostname": item["hostname"],
                "location": item["location"],
                "bytes_out": item["bytes_out"],
                "packets_out": item["packets_out"],
                "bytes_in": item["bytes_in"],
                "packets_in": item["packets_in"],
                "interval_mean": item["interval_mean"],
                "interval_cv": item["interval_cv"],
                "anomaly_score": round(finding.score, 4),
                "is_outlier": finding.model_label == -1,
                "baseline_sessions": int(sessions[entities.index(finding.entity_id)]),
                "first_seen": next(
                    row.first_seen for row in eligibility if row.entity_id == finding.entity_id
                ),
                "reasons": reasons,
                "evidence": [
                    {
                        "artifact_digest": str(pointer.artifact_digest),
                        "entity_id": pointer.entity_id.value if pointer.entity_id else None,
                        "selector": pointer.selector,
                    }
                    for pointer in finding.evidence
                ],
            }
        )
    return ranked


def score_host_outbound(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    entities = tuple(EntityId(f"ip:{row['ip_address']}") for row in rows)
    raw = HOST_DESTINATION_FEATURE_REGISTRY_V1.raw_matrix(rows)
    transformed = HOST_DESTINATION_FEATURE_REGISTRY_V1.transform(raw)
    center = np.tile(np.median(transformed, axis=0), (len(rows), 1))
    frames = tuple(
        tuple(ComparisonFrame.PEER for _ in HOST_DESTINATION_NUMERIC_FEATURE_SET_V1.features)
        for _ in rows
    )
    reference = ReferenceResult(
        "peer",
        "1",
        entities,
        HOST_DESTINATION_NUMERIC_FEATURE_SET_V1.feature_names,
        tuple(tuple(float(value) for value in row) for row in center),
        frames,
        tuple(ReferenceEntity(entity, ReferenceEligibility.PEER) for entity in entities),
        entities,
    )
    result = LegacyBehavioralRanker(HOST_DESTINATION_FEATURE_REGISTRY_V1).rank(
        entities,
        _representation(entities, raw, transformed, host=True),
        reference,
        RankerSpec(
            ranker_id="legacy_2_0",
            version="1",
            seed=0,
            parameters={"active_features": tuple(HOST_OUTBOUND_FEATURES)},
        ),
    )
    original = {EntityId(f"ip:{row['ip_address']}"): row for row in rows}
    explanations = ExplanationService(HOST_DESTINATION_FEATURE_REGISTRY_V1)
    ranked = []
    for finding in result.findings:
        row = original[finding.entity_id]
        explanation = explanations.explain(
            finding, is_local=True, cloud_hosted=_cloud_hosted(row.get("org"))
        )
        reasons = [
            {
                "feature": reason.feature,
                "value": round(reason.value, 4),
                "unit": reason.unit,
                "robust_z": round(reason.standardized_deviation, 2),
                "direction": reason.direction,
                "host_relative": reason.host_relative,
            }
            for reason in explanation.reasons
            if reason.feature in HOST_OUTBOUND_FEATURES
        ]
        ranked.append(
            {
                **row,
                "cloud_hosted": _cloud_hosted(row.get("org")),
                "upload_download_ratio": round(
                    float(row["upload_bytes"]) / (float(row["download_bytes"]) + 1.0), 4
                ),
                "outbound_score": round(finding.score, 4),
                "is_flagged": bool(reasons),
                "reasons": reasons,
                "evidence": [
                    {
                        "artifact_digest": str(finding.evidence[0].artifact_digest),
                        "entity_id": finding.entity_id.value,
                        "selector": finding.evidence[0].selector,
                    }
                ],
            }
        )
    return ranked


def build_feature_matrix(
    embeddings: Sequence[Sequence[float]],
    data: Sequence[Mapping[str, object]],
    components: int,
    whiten: bool,
    feature_weight: float,
) -> tuple[np.ndarray, Any]:
    """Legacy tuple adapter; PCA object is retained for old callers."""
    from sklearn.decomposition import PCA  # type: ignore[import-untyped]
    from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

    vectors = np.asarray(embeddings, dtype=float)
    pca = PCA(n_components=components, whiten=whiten, random_state=0)
    text = pca.fit_transform(vectors)
    if feature_weight <= 0:
        return cast(np.ndarray, text), pca
    numeric = StandardScaler().fit_transform(
        transform_numeric_features(build_numeric_features(data))
    )
    return np.hstack((text, feature_weight * numeric)), pca


def recommend_eps(features: np.ndarray, min_samples: int) -> float:
    return KDistanceEpsilonStrategy().recommend(features, min_samples).value
