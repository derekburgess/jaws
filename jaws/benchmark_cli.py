"""Run governed Benchmark v1 profiles through the research experiment service."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jaws.adapters.benchmark_datasets import (
    load_benchmark_policy,
    load_dataset_manifest,
    scenario_data,
)
from jaws.adapters.benchmark_reports import BenchmarkReportRenderer
from jaws.adapters.experiment_bundles import ExperimentBundleStore
from jaws.adapters.provenance import ProvenanceCollector
from jaws.domain import (
    BenchmarkMatrixSpec,
    BenchmarkPartition,
    CaptureId,
    ComponentSpec,
    DatasetManifest,
    ExperimentSpec,
    ObservationWindow,
    RankerSpec,
    ReferenceKind,
    ReferenceSpec,
    RepresentationSpec,
    canonical_digest,
)
from jaws.services.benchmark import BenchmarkDatasetRegistry, BenchmarkRunner
from jaws.services.benchmark_rankers import BenchmarkRegistries, default_benchmark_registries
from jaws.services.research import (
    ComponentDescriptor,
    ExperimentService,
    ResearchCatalog,
    ResearchRunRepository,
)

REPOSITORY = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = Path(os.environ.get("JAWS_BENCHMARK_ROOT", str(REPOSITORY / "benchmarks" / "v1")))
SMOKE_SCENARIOS = ("software_updates", "periodic_beacon")


def run_benchmark(
    tier: str,
    output_dir: Path,
    bundle_dir: Path,
    *,
    include_held_out: bool = False,
    benchmark_root: Path = BENCHMARK_ROOT,
) -> tuple[Path, Path, Path]:
    policy_path = benchmark_root / "policy.json"
    policy = load_benchmark_policy(policy_path)
    synthetic = load_dataset_manifest(benchmark_root / "datasets" / "jaws-synthetic-v1.json")
    if synthetic.dataset_id is None:
        raise RuntimeError("synthetic benchmark manifest has no dataset identity")
    datasets = BenchmarkDatasetRegistry()
    datasets.register(synthetic, scenario_data(synthetic))
    registries = default_benchmark_registries()
    ranker_ids = tuple(item.component_id for item in registries.rankers.list())
    catalog = _research_catalog(synthetic, registries)
    experiments = ExperimentService(
        catalog,
        ResearchRunRepository(),
        ExperimentBundleStore(bundle_dir),
        ProvenanceCollector(REPOSITORY),
    )
    tier_policy = _tier_policy(policy.tier_samples, tier)
    scenarios = _scenarios(synthetic, tier, include_held_out)
    seeds = _integer_sequence(tier_policy, "seeds")
    windows = tuple(str(value) for value in _sequence(tier_policy, "windows"))
    base = ExperimentSpec(
        hypothesis="Benchmark v1 compares attention allocation against simple baselines",
        observation=ObservationWindow(capture_ids=(CaptureId("benchmark-placeholder"),)),
        representation=RepresentationSpec(
            representation_id="numeric",
            version="1",
            parameters={
                "feature_version": "endpoint-numeric-v1",
                "template_version": "none",
                "model_version": "none",
                "normalization": "declared-by-ranker",
            },
        ),
        reference=ReferenceSpec(kind=ReferenceKind.PEER, version="1"),
        ranker=RankerSpec(ranker_id="seeded_random", version="1", seed=seeds[0]),
        evaluator=ComponentSpec(component_id="reward_vector", version="1"),
        renderer=ComponentSpec(component_id="json", version="1"),
        deterministic_settings={"threads": 1, "tier": tier},
    )
    matrix = BenchmarkMatrixSpec(
        experiment=base,
        dataset_ids=(synthetic.dataset_id,),
        scenario_ids=scenarios,
        representation_ids=("numeric",),
        reference_kinds=(ReferenceKind.PEER,),
        ranker_ids=ranker_ids,
        seeds=seeds,
        windows=windows,
    )
    code_commit, dirty_tree = _repository_state(REPOSITORY)
    runner = BenchmarkRunner(
        datasets,
        registries,
        experiments,
        policy,
        environment={
            "tier": tier,
            "code_commit": code_commit,
            "dirty_tree": dirty_tree,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "policy_digest": str(canonical_digest(policy)),
            "dataset_manifest_version": synthetic.manifest_version,
            "held_out_labels_enabled": include_held_out,
        },
    )
    report = runner.run(matrix, allow_held_out_labels=include_held_out)
    renderer = BenchmarkReportRenderer()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = (
        output_dir / "benchmark-v1.json",
        output_dir / "benchmark-v1.md",
        output_dir / "benchmark-v1.html",
    )
    paths[0].write_text(renderer.json(report), encoding="utf-8")
    paths[1].write_text(renderer.markdown(report), encoding="utf-8")
    paths[2].write_text(renderer.html(report), encoding="utf-8")
    return paths


def _repository_state(repository: Path) -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", True
    commit = revision.stdout.strip() if revision.returncode == 0 else "unknown"
    return commit, status.returncode != 0 or bool(status.stdout.strip())


def _research_catalog(
    manifest: DatasetManifest, registries: BenchmarkRegistries
) -> ResearchCatalog:
    dataset_id = manifest.dataset_id
    if dataset_id is None:
        raise ValueError("benchmark manifest has no dataset identity")
    catalog = ResearchCatalog(
        datasets={dataset_id.value},
        captures={capture.value for item in manifest.scenarios for capture in item.capture_ids},
        labels={f"{dataset_id.value}:{manifest.label_source_version}"},
    )
    for metadata in (
        *registries.representations.list(),
        *registries.references.list(),
        *registries.rankers.list(),
        *registries.evaluators.list(),
        *registries.renderers.list(),
    ):
        catalog.register(
            ComponentDescriptor(metadata.kind, metadata.component_id, metadata.version)
        )
    return catalog


def _tier_policy(tiers: Mapping[str, Any], tier: str) -> Mapping[str, Any]:
    value = tiers.get(tier)
    if not isinstance(value, Mapping):
        raise ValueError(f"benchmark policy has no tier: {tier}")
    return value


def _sequence(document: Mapping[str, Any], name: str) -> Sequence[object]:
    value = document.get(name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError(f"benchmark tier {name} must be a nonempty array")
    return value


def _integer_sequence(document: Mapping[str, Any], name: str) -> tuple[int, ...]:
    values = _sequence(document, name)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise ValueError(f"benchmark tier {name} must contain integers")
    return tuple(value for value in values if isinstance(value, int))


def _scenarios(manifest: DatasetManifest, tier: str, include_held_out: bool) -> tuple[str, ...]:
    if tier == "smoke":
        return SMOKE_SCENARIOS
    allowed = {BenchmarkPartition.DEVELOPMENT, BenchmarkPartition.VALIDATION}
    if include_held_out:
        allowed.add(BenchmarkPartition.HELD_OUT)
    return tuple(item.scenario_id for item in manifest.scenarios if item.partition in allowed)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-v1"))
    parser.add_argument("--bundle-dir", type=Path)
    parser.add_argument(
        "--include-held-out",
        action="store_true",
        help="Explicitly enable held-out labels for a governed evaluation run.",
    )
    args = parser.parse_args(argv)
    output_dir = Path(args.output_dir)
    bundle_dir = Path(args.bundle_dir) if args.bundle_dir else output_dir / "run-bundles"
    paths = run_benchmark(
        str(args.tier),
        output_dir,
        bundle_dir,
        include_held_out=bool(args.include_held_out),
    )
    print(f"Benchmark v1 {args.tier} report: {paths[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
