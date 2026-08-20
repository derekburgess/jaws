"""Contracts for initial service ports and deterministic in-memory implementations."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from jaws.domain import (
    CaptureId,
    EmbeddingInput,
    EntityId,
    EvidencePointer,
    FindingId,
    ObservationWindow,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
    RunId,
    Score,
    ScoreDirection,
)
from jaws.ports import (
    FakeEmbeddingProvider,
    FakeEnrichmentProvider,
    FakeEvaluator,
    FakeRanker,
    FakeReferenceBuilder,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryEvidenceStore,
    SequenceIdGenerator,
    SequencePacketSource,
)


def _window(*capture_ids: str) -> ObservationWindow:
    return ObservationWindow(capture_ids=tuple(CaptureId(value) for value in capture_ids))


def _finding() -> RankedFinding:
    return RankedFinding(
        finding_id=FindingId("finding-a"),
        entity_id=EntityId("entity-a"),
        rank=1,
        score=Score(1.0, ScoreDirection.HIGHER_IS_MORE_ANOMALOUS),
        evidence=(EvidencePointer(capture_id=CaptureId("capture-a")),),
    )


def test_evidence_fake_is_append_only_capture_scoped_and_ordered():
    store = InMemoryEvidenceStore[str]()
    store.append(CaptureId("capture-a"), ["a1", "a2"])
    store.append(CaptureId("capture-b"), ["b1"])
    store.append(CaptureId("capture-a"), ["a3"])

    assert store.read(_window("capture-b", "capture-a")) == ("b1", "a1", "a2", "a3")
    assert store.read(_window("missing")) == ()


def test_artifact_fake_returns_content_digest_and_bytes():
    store = InMemoryArtifactStore()
    content = b"evidence"

    digest = store.put("runs/run-a.json", content)

    assert str(digest) == "ee8250fb76e094b34b471f13a73dbbe51d1ae142e9df59d7c0d31ec20f0a0a8e"
    assert store.get("runs/run-a.json") == b"evidence"
    with pytest.raises(KeyError):
        store.get("missing")


def test_packet_source_replays_the_same_bounded_sequence():
    source = SequencePacketSource(({"packet": 1}, {"packet": 2}))

    assert list(source.packets()) == [{"packet": 1}, {"packet": 2}]
    assert list(source.packets()) == [{"packet": 1}, {"packet": 2}]


def test_enrichment_fake_returns_scripted_values_and_records_requests():
    provider = FakeEnrichmentProvider({EntityId("entity-a"): {"asn": "AS64500"}})

    assert provider.enrich(EntityId("entity-a")) == {"asn": "AS64500"}
    assert provider.requests == [EntityId("entity-a")]


def test_embedding_fake_preserves_input_order_and_records_batches():
    provider = FakeEmbeddingProvider({"first": (1.0, 0.0), "second": (0.0, 1.0)})
    inputs = (EmbeddingInput("profile-second", "second"), EmbeddingInput("profile-first", "first"))

    result = provider.embed(inputs)

    assert tuple(vector.values for vector in result.vectors) == ((0.0, 1.0), (1.0, 0.0))
    assert tuple(vector.input_text_digest for vector in result.vectors) == tuple(
        item.text_digest for item in inputs
    )
    assert result.provider == provider.spec
    assert provider.requests == [inputs]


def test_ranker_fake_returns_complete_findings_and_records_spec():
    finding = _finding()
    ranker = FakeRanker[str]((finding,))
    spec = RankerSpec(ranker_id="fixture")

    assert ranker.rank(["candidate-b", "candidate-a"], spec) == (finding,)
    assert ranker.requests == [(("candidate-b", "candidate-a"), spec)]


def test_reference_and_evaluator_fakes_record_bounded_inputs():
    reference_spec = ReferenceSpec()
    builder = FakeReferenceBuilder[str, dict[str, int]]({"count": 2})
    finding = _finding()
    evaluator = FakeEvaluator[dict[str, bool], dict[str, float]]({"recall": 1.0})

    assert builder.build(["a", "b"], reference_spec) == {"count": 2}
    assert builder.requests == [(("a", "b"), reference_spec)]
    assert evaluator.evaluate([finding], {"entity-a": True}) == {"recall": 1.0}
    assert evaluator.requests == [((finding,), {"entity-a": True})]


def test_clock_normalizes_to_utc_and_id_generator_is_explicitly_finite():
    clock = FrozenClock(datetime(2026, 8, 10, 8, tzinfo=timezone(-timedelta(hours=4))))
    generator = SequenceIdGenerator((RunId("run-a"), RunId("run-b")))

    assert clock.now() == datetime(2026, 8, 10, 12, tzinfo=UTC)
    assert generator.new() == RunId("run-a")
    assert generator.new() == RunId("run-b")
    with pytest.raises(IndexError):
        generator.new()
