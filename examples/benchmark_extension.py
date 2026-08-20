"""Minimal bounded Benchmark v1 ranker and scenario extension."""

from __future__ import annotations

from jaws.domain import (
    BenchmarkCandidate,
    EntityId,
    RankerSpec,
)
from jaws.services.benchmark_rankers import (
    ExampleMaximumFeatureRanker,
    PluginMetadata,
    PluginRegistration,
    default_benchmark_registries,
)


def main() -> None:
    registries = default_benchmark_registries()
    registries.rankers.register(
        PluginRegistration(
            PluginMetadata("ranker", "example_max_bytes_out", "1"),
            ExampleMaximumFeatureRanker,
        )
    )
    candidates = (
        BenchmarkCandidate(EntityId("example-normal"), {"bytes_out": 10.0}),
        BenchmarkCandidate(EntityId("example-target"), {"bytes_out": 100.0}),
    )
    ranker = registries.rankers.resolve("example_max_bytes_out", "1")
    findings = ranker.rank(
        candidates,
        RankerSpec(ranker_id="example_max_bytes_out", version="1", seed=7),
    )
    print(tuple(item.entity_id.value for item in findings if item.entity_id is not None))


if __name__ == "__main__":
    main()
