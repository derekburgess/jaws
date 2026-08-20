"""Versioned endpoint numeric-feature definitions and legacy matrix parity."""

from dataclasses import replace

import numpy as np
import pytest

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    HOST_DESTINATION_NUMERIC_FEATURE_SET_V1,
    MeasurementUnit,
    MissingValuePolicy,
    NumericAnalysisTransformation,
    NumericFeatureDefinition,
    NumericFeatureFamily,
    NumericFeatureSet,
    NumericFeatureTransformation,
    TimingDirection,
    TimingDirectionSelection,
    TimingEvidenceRequirement,
    TimingIntervalScope,
)
from jaws.jaws_finder import (
    FEATURE_UNITS,
    NUMERIC_FEATURE_NAMES,
    build_numeric_features,
    transform_numeric_features,
)

EXPECTED_FEATURES = (
    ("bytes_out", "base", "bytes", "identity", "forbid"),
    ("bytes_in", "base", "bytes", "identity", "forbid"),
    ("packets_out", "base", "packets", "identity", "forbid"),
    ("packets_in", "base", "packets", "identity", "forbid"),
    ("out_peers", "base", "peers", "identity", "forbid"),
    ("in_peers", "base", "peers", "identity", "forbid"),
    ("bytes_out_in_ratio", "shape", "ratio", "safe_ratio", "forbid"),
    ("packets_out_in_ratio", "shape", "ratio", "safe_ratio", "forbid"),
    ("bytes_per_packet", "shape", "bytes/packet", "safe_ratio", "forbid"),
    ("bytes_per_peer", "shape", "bytes/peer", "safe_ratio", "forbid"),
    (
        "interval_mean",
        "timing",
        "seconds",
        "identity",
        "population_median_or_zero",
    ),
    ("interval_cv", "timing", "ratio", "identity", "population_median_or_zero"),
)


def _profile(**overrides):
    values = {
        "bytes_out": 100,
        "bytes_in": 9,
        "packets_out": 4,
        "packets_in": 1,
        "out_peers": 2,
        "in_peers": 1,
        "interval_mean": 1.0,
        "interval_cv": 0.2,
    }
    values.update(overrides)
    return values


def test_endpoint_numeric_feature_set_versions_exact_order_semantics_and_units():
    feature_set = ENDPOINT_NUMERIC_FEATURE_SET_V1

    assert feature_set.feature_set_id == "endpoint_profile_numeric"
    assert feature_set.version == "1"
    assert (
        tuple(
            (
                feature.name,
                feature.family.value,
                feature.unit.value,
                feature.transformation.value,
                feature.missing_value_policy.value,
            )
            for feature in feature_set.features
        )
        == EXPECTED_FEATURES
    )
    assert all(
        feature.analysis_transformation is NumericAnalysisTransformation.LOG1P
        for feature in feature_set.features
    )
    assert tuple(feature.numerator_fields for feature in feature_set.features) == (
        ("bytes_out",),
        ("bytes_in",),
        ("packets_out",),
        ("packets_in",),
        ("out_peers",),
        ("in_peers",),
        ("bytes_out",),
        ("packets_out",),
        ("bytes_out", "bytes_in"),
        ("bytes_out",),
        ("interval_mean",),
        ("interval_cv",),
    )
    assert tuple(feature.denominator_fields for feature in feature_set.features[6:10]) == (
        ("bytes_in",),
        ("packets_in",),
        ("packets_out", "packets_in"),
        ("out_peers",),
    )
    assert tuple(feature.denominator_offset for feature in feature_set.features[6:10]) == (
        1.0,
        1.0,
        1.0,
        1.0,
    )
    assert tuple(NUMERIC_FEATURE_NAMES) == feature_set.feature_names
    assert FEATURE_UNITS == {feature.name: feature.unit.value for feature in feature_set.features}
    assert feature_set.timing_evidence == TimingEvidenceRequirement(
        directions=(TimingDirection.OUTBOUND, TimingDirection.INBOUND),
        selection=TimingDirectionSelection.LOWEST_COEFFICIENT_OF_VARIATION,
        minimum_packets_per_direction=6,
        interval_scope=TimingIntervalScope.WITHIN_CAPTURE,
    )


def test_host_destination_numeric_feature_set_is_versioned_without_embedding_or_timing():
    feature_set = HOST_DESTINATION_NUMERIC_FEATURE_SET_V1

    assert feature_set.feature_set_id == "host_destination_numeric"
    assert feature_set.version == "1"
    assert feature_set.feature_names == (
        "upload_bytes",
        "upload_packets",
        "download_bytes",
        "download_packets",
        "upload_download_ratio",
    )
    assert feature_set.timing_evidence is None
    ratio = feature_set.features[-1]
    assert ratio.transformation is NumericFeatureTransformation.SAFE_RATIO
    assert ratio.numerator_fields == ("upload_bytes",)
    assert ratio.denominator_fields == ("download_bytes",)
    assert ratio.denominator_offset == 1.0


def test_declared_transformations_preserve_legacy_matrix_and_missing_value_behavior():
    rows = (
        _profile(),
        _profile(
            bytes_out=0,
            bytes_in=0,
            packets_out=0,
            packets_in=0,
            out_peers=0,
            in_peers=0,
            interval_mean=None,
            interval_cv=None,
        ),
        _profile(interval_mean=5.0, interval_cv=0.6),
    )

    matrix = build_numeric_features(rows)

    assert matrix.shape == (3, 12)
    assert matrix[0] == pytest.approx((100, 9, 4, 1, 2, 1, 10, 2, 109 / 6, 100 / 3, 1, 0.2))
    assert matrix[1] == pytest.approx((0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0.4))
    assert np.array_equal(transform_numeric_features(matrix), np.log1p(matrix))


def test_all_missing_timing_uses_declared_zero_fallback_and_empty_input_keeps_width():
    missing = build_numeric_features((_profile(interval_mean=None, interval_cv=None),))
    empty = build_numeric_features(())

    assert tuple(missing[0, -2:]) == (0.0, 0.0)
    assert empty.shape == (0, len(ENDPOINT_NUMERIC_FEATURE_SET_V1.features))


def test_numeric_feature_contract_rejects_ambiguous_definitions():
    with pytest.raises(ValueError, match="exactly one source"):
        NumericFeatureDefinition(
            "bad_identity",
            NumericFeatureFamily.BASE,
            MeasurementUnit.BYTES,
            NumericFeatureTransformation.IDENTITY,
            ("left", "right"),
        )
    with pytest.raises(ValueError, match="denominator fields and positive offset"):
        NumericFeatureDefinition(
            "bad_ratio",
            NumericFeatureFamily.SHAPE,
            MeasurementUnit.RATIO,
            NumericFeatureTransformation.SAFE_RATIO,
            ("left",),
        )
    duplicate = replace(ENDPOINT_NUMERIC_FEATURE_SET_V1.features[1], name="bytes_out")
    with pytest.raises(ValueError, match="names must be unique"):
        NumericFeatureSet(
            feature_set_id="duplicate",
            features=(ENDPOINT_NUMERIC_FEATURE_SET_V1.features[0], duplicate),
        )
    with pytest.raises(ValueError, match="feature-set ID"):
        replace(ENDPOINT_NUMERIC_FEATURE_SET_V1, feature_set_id=" ")


def test_missing_value_policy_is_part_of_feature_set_identity():
    timing = ENDPOINT_NUMERIC_FEATURE_SET_V1.features[-1]
    changed_timing = replace(timing, missing_value_policy=MissingValuePolicy.FORBID)
    changed = replace(
        ENDPOINT_NUMERIC_FEATURE_SET_V1,
        features=ENDPOINT_NUMERIC_FEATURE_SET_V1.features[:-1] + (changed_timing,),
    )

    assert changed.digest != ENDPOINT_NUMERIC_FEATURE_SET_V1.digest


def test_timing_evidence_policy_is_required_and_part_of_feature_set_identity():
    feature_set = ENDPOINT_NUMERIC_FEATURE_SET_V1
    changed = replace(
        feature_set,
        timing_evidence=replace(
            feature_set.timing_evidence,
            minimum_packets_per_direction=7,
        ),
    )

    assert changed.digest != feature_set.digest
    with pytest.raises(ValueError, match="require timing evidence metadata"):
        replace(feature_set, timing_evidence=None)
    with pytest.raises(ValueError, match="at least two packets"):
        replace(feature_set.timing_evidence, minimum_packets_per_direction=1)
