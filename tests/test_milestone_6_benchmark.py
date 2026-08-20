from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from jaws.adapters.benchmark_datasets import load_benchmark_policy, load_dataset_manifest
from jaws.adapters.benchmark_reports import BenchmarkReportRenderer
from jaws.adapters.experiment_bundles import ExperimentBundleStore
from jaws.adapters.provenance import ProvenanceCollector
from jaws.domain import (
    BenchmarkCandidate,
    BenchmarkMatrixSpec,
    BenchmarkPartition,
    BenchmarkPolicy,
    CanonicalDigest,
    CaptureId,
    ComponentSpec,
    DatasetId,
    DatasetManifest,
    EntityId,
    EntityType,
    EvidencePointer,
    ExperimentSpec,
    LabelRecord,
    ObservationWindow,
    RankerSpec,
    RedistributionPolicy,
    ReferenceKind,
    ReferenceSpec,
    RepresentationSpec,
    ScenarioData,
    ScenarioManifest,
    ScenarioStatus,
)
from jaws.services.benchmark import (
    BenchmarkArtifactCache,
    BenchmarkDatasetRegistry,
    BenchmarkRunner,
    CacheCompatibility,
)
from jaws.services.benchmark_metrics import (
    evaluate_reward_vector,
    false_positive_movement,
    stability_metrics,
    validate_comparative_claim,
)
from jaws.services.benchmark_rankers import (
    PLUGIN_SCHEMA_VERSION,
    PluginMetadata,
    PluginRegistration,
    PluginRegistry,
    default_benchmark_registries,
)
from jaws.services.research import (
    ComponentDescriptor,
    ExperimentService,
    ResearchCatalog,
    ResearchRunRepository,
)

NOW = datetime(2026, 8, 20, tzinfo=UTC)
SHA_A = CanonicalDigest("a" * 64)
SHA_B = CanonicalDigest("b" * 64)


def _candidate(name: str, factor: float, *, target: bool = False) -> BenchmarkCandidate:
    capture = CaptureId("synthetic-capture")
    return BenchmarkCandidate(
        EntityId(name),
        {
            "bytes_out": factor * (8000 if target else 100),
            "bytes_in": factor * 100,
            "packets_out": factor * (80 if target else 10),
            "packets_in": factor * 10,
            "out_peers": factor,
            "in_peers": factor,
            "interval_mean": 10 / factor,
            "interval_cv": 0.01 * factor,
        },
        history={
            "bytes_out": factor * 100,
            "bytes_in": factor * 100,
            "packets_out": factor * 10,
            "packets_in": factor * 10,
            "out_peers": factor,
            "in_peers": factor,
            "interval_mean": 10 / factor,
            "interval_cv": 0.01 * factor,
        },
        embedding=(factor, factor / 2, 10 if target else 0),
        first_seen=target,
        evidence=(EvidencePointer(capture_id=capture, entity_id=EntityId(name)),),
    )


def _scenario(
    scenario_id: str = "periodic_beacon",
    *,
    partition: BenchmarkPartition = BenchmarkPartition.DEVELOPMENT,
    available: bool = True,
) -> ScenarioData:
    candidates = tuple(
        _candidate("target", 1, target=True)
        if index == 0
        else _candidate(f"benign-{index}", float(index + 1))
        for index in range(6)
    )
    labels = (
        LabelRecord(EntityId("target"), 3, "periodic_beacon", "labels-1"),
        *(
            LabelRecord(EntityId(f"benign-{index}"), 0, "updates", "labels-1")
            for index in range(1, 6)
        ),
    )
    manifest = ScenarioManifest(
        scenario_id=scenario_id,
        family="periodic_beacon",
        partition=partition,
        capture_ids=(CaptureId("synthetic-capture"),),
        evidence_digest=SHA_A,
        labels=labels,
        synthetic=True,
        model_author="JAWS deterministic fixture generator v1",
        expected_behavior="The labeled target should move toward the top of anomaly rankings.",
        known_limitations=(
            "Controlled model-authored traffic, not a quality claim about real traffic.",
        ),
        available=available,
    )
    return ScenarioData(manifest, candidates if available else ())


def _dataset(*scenarios: ScenarioData) -> DatasetManifest:
    return DatasetManifest(
        dataset_id=DatasetId("synthetic-v1"),
        manifest_version="1.0.0",
        title="JAWS controlled benchmark scenarios",
        source_url="https://github.com/derekburgess/jaws",
        source_location="examples/benchmark-v1/scenarios.json",
        acquisition_date=date(2026, 8, 20),
        safety_review_date=date(2026, 8, 20),
        license_name="GPL-2.0-only",
        license_url="https://www.gnu.org/licenses/old-licenses/gpl-2.0.html",
        redistribution=RedistributionPolicy.PERMITTED,
        checksum=SHA_B,
        capture_host="model-authored fixture",
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=1),
        label_source_version="labels-1",
        label_provenance="Scenario-authored target and benign-family labels retained in manifest.",
        scenarios=tuple(item.manifest for item in scenarios),
        known_limitations=(
            "Synthetic scenarios isolate behavior but do not establish field quality.",
        ),
    )


def _base_spec() -> ExperimentSpec:
    return ExperimentSpec(
        hypothesis="Benchmark template",
        observation=ObservationWindow(capture_ids=(CaptureId("synthetic-capture"),)),
        representation=RepresentationSpec(representation_id="numeric"),
        reference=ReferenceSpec(kind=ReferenceKind.PEER),
        ranker=RankerSpec(ranker_id="seeded_random", seed=0),
        evaluator=ComponentSpec(component_id="reward_vector"),
        renderer=ComponentSpec(component_id="json"),
    )


def _research_catalog(ranker_ids: tuple[str, ...]) -> ResearchCatalog:
    catalog = ResearchCatalog(datasets={"synthetic-v1"}, captures={"synthetic-capture"})
    for descriptor in (
        ComponentDescriptor("representation", "numeric", "1"),
        ComponentDescriptor("representation", "embedding", "1"),
        ComponentDescriptor("reference", "peer", "1"),
        ComponentDescriptor("reference", "historical", "1"),
        ComponentDescriptor("evaluator", "reward_vector", "1"),
        ComponentDescriptor("renderer", "json", "1"),
        *(ComponentDescriptor("ranker", ranker_id, "1") for ranker_id in ranker_ids),
    ):
        catalog.register(descriptor)
    return catalog


def test_dataset_governance_withholds_held_out_labels_and_forbids_binaries() -> None:
    development = _scenario()
    held_out = _scenario("held-out", partition=BenchmarkPartition.HELD_OUT)
    manifest = _dataset(development, held_out)
    tuning = manifest.tuning_view()
    assert tuning.manifest_version == manifest.manifest_version
    assert tuning.scenarios[0].labels
    assert tuning.scenarios[1].labels == ()
    assert tuning.scenarios[1].labels_withheld
    with pytest.raises(ValueError, match="malware binaries"):
        DatasetManifest(
            dataset_id=manifest.dataset_id,
            manifest_version=manifest.manifest_version,
            title=manifest.title,
            source_url=manifest.source_url,
            source_location=manifest.source_location,
            acquisition_date=manifest.acquisition_date,
            safety_review_date=manifest.safety_review_date,
            license_name=manifest.license_name,
            license_url=manifest.license_url,
            checksum=manifest.checksum,
            label_source_version=manifest.label_source_version,
            label_provenance=manifest.label_provenance,
            scenarios=manifest.scenarios,
            contains_malware_binaries=True,
        )


def test_every_baseline_ranker_uses_the_same_bounded_contract() -> None:
    candidates = _scenario().candidates
    registries = default_benchmark_registries()
    metadata = registries.rankers.list()
    assert {item.component_id for item in metadata} == {
        "seeded_random",
        "total_bytes",
        "bytes_out",
        "bytes_in",
        "first_seen",
        "upload_download_ratio",
        "peer_robust_deviation",
        "own_history_change",
        "numeric_current",
        "embedding_pca_dbscan",
        "legacy_2_0",
        "isolation_forest",
    }
    for plugin in metadata:
        ranker = registries.rankers.resolve(plugin.component_id, plugin.version)
        findings = ranker.rank(
            candidates,
            RankerSpec(ranker_id=plugin.component_id, version=plugin.version, seed=7),
        )
        assert tuple(item.rank for item in findings) == tuple(range(1, len(candidates) + 1))
        assert {item.entity_id for item in findings} == {item.entity_id for item in candidates}
        assert ranker.metadata.entity_types == (EntityType.ENDPOINT_IP,)
        assert ranker.metadata.reference_kinds == (ReferenceKind.PEER,)
        assert ranker.metadata.explanation_capability in {"none", "score", "feature", "model"}


def test_plugin_metadata_schema_and_duplicate_registration_are_rejected() -> None:
    registry: PluginRegistry[object] = PluginRegistry("renderer")
    registration = PluginRegistration(PluginMetadata("renderer", "example", "1"), object)
    registry.register(registration)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(registration)
    with pytest.raises(ValueError, match="schema"):
        PluginMetadata(
            "renderer", "future", "1", plugin_schema_version=f"{PLUGIN_SCHEMA_VERSION}.future"
        )


def test_complete_reward_vector_stability_and_claim_policy() -> None:
    scenario = _scenario()
    findings = (
        default_benchmark_registries()
        .rankers.resolve("total_bytes", "1")
        .rank(scenario.candidates, RankerSpec(ranker_id="total_bytes", seed=0))
    )
    reward = evaluate_reward_vector(findings, scenario.manifest.labels, explanation_fidelity=1.0)
    flattened = reward.flattened()
    assert {1, 3, 5, 10} == {item.k for item in reward.recall}
    assert {1, 3, 5, 10} == {item.k for item in reward.ndcg}
    assert reward.target_ranks[0].entity_id == EntityId("target")
    assert reward.explanation_fidelity == 1.0
    assert "mean_reciprocal_rank" in flattened
    assert "benign_above_first_relevant" in flattened
    ranking = tuple(item.entity_id for item in findings if item.entity_id)
    reversed_ranking = tuple(reversed(ranking))
    stability = stability_metrics(ranking, reversed_ranking, k=3, parameter_distance=0.1)
    assert 0 <= stability.top_k_overlap <= 1
    assert -1 <= stability.rank_correlation <= 1
    movement = false_positive_movement(ranking, reversed_ranking, scenario.manifest.labels, k=3)
    assert "updates" in movement
    worse = evaluate_reward_vector(
        tuple(reversed(findings)), scenario.manifest.labels, explanation_fidelity=1.0
    )
    if next(item.value for item in worse.recall if item.k == 3) > next(
        item.value for item in reward.recall if item.k == 3
    ):
        with pytest.raises(ValueError, match="recall-only"):
            validate_comparative_claim(reward, worse, recall_k=3)


def test_cache_identity_includes_every_output_affecting_dimension() -> None:
    cache = BenchmarkArtifactCache()
    calls = 0

    def compute() -> object:
        nonlocal calls
        calls += 1
        return (calls,)

    base = CacheCompatibility(
        SHA_A, "endpoint_ip", "numeric", "1", "1", "none", "none", "l2", "full"
    )
    assert cache.get_or_compute(base, compute) == (1,)
    assert cache.get_or_compute(base, compute) == (1,)
    assert cache.hits == 1
    for changed in (
        CacheCompatibility(SHA_B, "endpoint_ip", "numeric", "1", "1", "none", "none", "l2", "full"),
        CacheCompatibility(
            SHA_A, "host_destination", "numeric", "1", "1", "none", "none", "l2", "full"
        ),
        CacheCompatibility(SHA_A, "endpoint_ip", "numeric", "1", "2", "none", "none", "l2", "full"),
        CacheCompatibility(SHA_A, "endpoint_ip", "numeric", "1", "1", "2", "none", "l2", "full"),
        CacheCompatibility(SHA_A, "endpoint_ip", "numeric", "1", "1", "none", "2", "l2", "full"),
        CacheCompatibility(
            SHA_A, "endpoint_ip", "numeric", "1", "1", "none", "none", "none", "full"
        ),
        CacheCompatibility(SHA_A, "endpoint_ip", "numeric", "1", "1", "none", "none", "l2", "half"),
    ):
        cache.get_or_compute(changed, compute)
    assert cache.misses == 8


def test_matrix_runs_through_experiment_path_and_renders_one_retained_report(
    tmp_path: Path,
) -> None:
    scenario = _scenario()
    dataset = _dataset(scenario)
    datasets = BenchmarkDatasetRegistry()
    datasets.register(dataset, {scenario.manifest.scenario_id: scenario})
    ranker_ids = ("seeded_random", "total_bytes", "legacy_2_0")
    catalog = _research_catalog(ranker_ids)
    ticks = iter(NOW + timedelta(seconds=index) for index in range(1000))
    bundle_store = ExperimentBundleStore(tmp_path / "bundles")
    experiments = ExperimentService(
        catalog,
        ResearchRunRepository(),
        bundle_store,
        ProvenanceCollector(Path.cwd()),
        now=lambda: next(ticks),
    )
    cache = BenchmarkArtifactCache()
    runner = BenchmarkRunner(
        datasets,
        default_benchmark_registries(),
        experiments,
        BenchmarkPolicy(required_baselines=("seeded_random", "total_bytes")),
        cache=cache,
        environment={"tier": "test", "python": "3.12"},
    )
    matrix = BenchmarkMatrixSpec(
        experiment=_base_spec(),
        dataset_ids=(DatasetId("synthetic-v1"),),
        scenario_ids=(scenario.manifest.scenario_id,),
        representation_ids=("numeric",),
        ranker_ids=ranker_ids,
        seeds=(7, 11),
    )
    report = runner.run(matrix)
    assert len(report.results) == 6
    assert all(item.status is ScenarioStatus.COMPLETED for item in report.results)
    assert {item.ranker_id for item in report.results} == set(ranker_ids)
    assert report.aggregates
    assert report.paired_deltas
    assert report.coverage.completed == 6
    assert report.artifact_checksums
    assert cache.hits >= 5 and cache.misses == 1
    deterministic = [item for item in report.results if item.ranker_id == "total_bytes"]
    assert deterministic[0].ranking == deterministic[1].ranking
    assert deterministic[0].reward is not None
    assert deterministic[0].reward.stability is not None
    assert deterministic[0].reward.stability.rank_correlation == 1.0
    for result in report.results:
        assert result.run_id is not None
        assert result.artifact_digest is not None
        assert result.parameter_digest is not None
        assert report.artifact_checksums[result.run_id.value] == result.artifact_digest
        bundles = tuple((tmp_path / "bundles").glob(f"experiments/*/runs/{result.run_id.value}"))
        assert len(bundles) == 1 and bundle_store.verify(bundles[0]).valid

    renderer = BenchmarkReportRenderer()
    json_report = renderer.json(report)
    markdown_report = renderer.markdown(report)
    html_report = renderer.html(report)
    assert report.benchmark_id in json_report
    assert "Required simple baselines" in markdown_report
    assert "legacy_2_0" in markdown_report and "total_bytes" in markdown_report
    assert html_report.startswith("<!doctype html>")


def test_matrix_reports_held_out_missing_and_abstained_separately(tmp_path: Path) -> None:
    held_out = _scenario("held", partition=BenchmarkPartition.HELD_OUT)
    unavailable = _scenario("real-unavailable", available=False)
    dataset = _dataset(held_out, unavailable)
    datasets = BenchmarkDatasetRegistry()
    datasets.register(dataset, {"held": held_out, "real-unavailable": unavailable})
    catalog = _research_catalog(("embedding_pca_dbscan",))
    service = ExperimentService(
        catalog,
        ResearchRunRepository(),
        ExperimentBundleStore(tmp_path / "bundles"),
        ProvenanceCollector(Path.cwd()),
    )
    runner = BenchmarkRunner(
        datasets,
        default_benchmark_registries(),
        service,
        BenchmarkPolicy(required_baselines=()),
    )
    matrix = BenchmarkMatrixSpec(
        experiment=_base_spec(),
        dataset_ids=(DatasetId("synthetic-v1"),),
        scenario_ids=("held", "real-unavailable", "not-registered"),
        representation_ids=("embedding",),
        ranker_ids=("embedding_pca_dbscan",),
    )
    report = runner.run(matrix)
    assert tuple(item.status for item in report.results) == (
        ScenarioStatus.SKIPPED,
        ScenarioStatus.MISSING,
        ScenarioStatus.MISSING,
    )


def test_governed_catalog_and_policy_validate_against_versioned_schemas() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_root = root / "docs" / "schemas" / "benchmark"
    dataset_validator = Draft202012Validator(
        json.loads((schema_root / "dataset-manifest.schema.json").read_text()),
        format_checker=FormatChecker(),
    )
    manifests = []
    for path in sorted((root / "benchmarks" / "v1" / "datasets").glob("*.json")):
        document = json.loads(path.read_text())
        dataset_validator.validate(document)
        manifests.append(load_dataset_manifest(path))
    assert len(manifests) == 5
    synthetic = next(
        item for item in manifests if item.dataset_id == DatasetId("jaws-synthetic-v1")
    )
    assert len(synthetic.scenarios) == 18
    assert {item.partition for item in synthetic.scenarios} == set(BenchmarkPartition)
    external = [item for item in manifests if item is not synthetic]
    assert all(item.acquisition_date is None and item.checksum is None for item in external)
    assert all(not scenario.available for item in external for scenario in item.scenarios)
    assert all(not item.contains_malware_binaries for item in manifests)

    policy_path = root / "benchmarks" / "v1" / "policy.json"
    policy_document = json.loads(policy_path.read_text())
    policy_schema = json.loads((schema_root / "policy.schema.json").read_text())
    Draft202012Validator(policy_schema).validate(policy_document)
    policy = load_benchmark_policy(policy_path)
    assert policy.required_baselines == ("seeded_random", "total_bytes")
    assert not (set(policy.expected_failures) & set(policy.accepted_regressions))


def test_public_smoke_profile_and_example_extension_are_runnable(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "report"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jaws.benchmark_cli",
            "--tier",
            "smoke",
            "--output-dir",
            str(output),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads((output / "benchmark-v1.json").read_text())
    report_schema = json.loads(
        (root / "docs" / "schemas" / "benchmark" / "report.schema.json").read_text()
    )
    Draft202012Validator(report_schema).validate(report)
    reward_schema = json.loads(
        (root / "docs" / "schemas" / "benchmark" / "reward-vector.schema.json").read_text()
    )
    reward_validator = Draft202012Validator(reward_schema)
    assert report["coverage"] == {
        "total": 48,
        "completed": 48,
        "missing": 0,
        "skipped": 0,
        "failed": 0,
        "abstained": 0,
    }
    assert len({row["ranker_id"] for row in report["results"]}) == 12
    assert report["environment"]["code_commit"]
    assert isinstance(report["environment"]["dirty_tree"], bool)
    assert all(row["reward"] is not None for row in report["results"])
    for row in report["results"]:
        reward_validator.validate(row["reward"])
    assert (output / "benchmark-v1.md").is_file()
    assert (output / "benchmark-v1.html").is_file()

    extension = subprocess.run(
        [sys.executable, "-m", "examples.benchmark_extension"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert extension.returncode == 0, extension.stderr
    assert extension.stdout.strip() == "('example-target', 'example-normal')"
