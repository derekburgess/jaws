"""Offline finding-index domain and repository contracts."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from finding_repository_contract import (
    _finding,
    assert_finding_repository_contract,
)

from jaws.domain import CanonicalDigest, FindingIndexBatch, RunId
from jaws.ports import InMemoryExperimentIndexRepository, InMemoryFindingRepository


def test_in_memory_finding_repository_follows_contract():
    experiments = InMemoryExperimentIndexRepository()
    repositories = SimpleNamespace(
        experiments=experiments,
        findings=InMemoryFindingRepository(experiments),
    )

    assert_finding_repository_contract(repositories)


def test_finding_index_batch_requires_one_complete_ranking_for_one_run():
    run_id = RunId("run_batch_contract")
    first = _finding("finding_batch_first", run_id, "ip:192.0.2.10", 1, 2.0)
    second = _finding("finding_batch_second", run_id, "ip:198.51.100.20", 2, 1.0)
    digest = CanonicalDigest("a" * 64)

    with pytest.raises(ValueError, match="published run"):
        FindingIndexBatch(
            run_id,
            digest,
            (first, replace(second, run_id=RunId("other"))),
        )
    with pytest.raises(ValueError, match="entities must be unique"):
        FindingIndexBatch(
            run_id,
            digest,
            (first, replace(second, entity_id=first.entity_id)),
        )
    with pytest.raises(ValueError, match="contiguous"):
        FindingIndexBatch(run_id, digest, (first, replace(second, rank=3)))
    with pytest.raises(ValueError, match="finite"):
        replace(first, score=float("nan"))
