"""Detection benchmark: does JAWS rank planted traffic it should, and ignore what it shouldn't?

Deselected by default (`-m recall`) because this measures detector QUALITY rather than
correctness — a change that moves recall is a finding to look at, not necessarily a
regression to block a commit on.

    JAWS_RECALL_SOURCE=synthetic python -m pytest -m recall -s

Set JAWS_RECALL_SOURCE to ``synthetic`` or ``pcap`` to run those quality tiers
independently. In ``all`` mode, real-capture scenarios are included automatically when
JAWS_PCAP_DIR points at a directory holding the samples named in harness/pcap.py.
"""
import pytest

from harness import all_scenarios, evaluate, recall_source, report

SOURCE = recall_source()
SCENARIOS = all_scenarios(SOURCE)

pytestmark = [pytest.mark.recall]
if not SCENARIOS:
    pytestmark.append(pytest.mark.skip(
        reason=(
            "No real-PCAP scenarios are available. Set JAWS_PCAP_DIR to a directory "
            "containing the documented capture fixtures."
            if SOURCE == "pcap"
            else f"No recall scenarios are available for source {SOURCE!r}."
        )
    ))

DETECT = [s for s in SCENARIOS if s.expect == "detect"]
REJECT = [s for s in SCENARIOS if s.expect == "reject"]


@pytest.fixture(scope="module")
def results():
    return [evaluate(s) for s in SCENARIOS]


def test_print_recall_table(results):
    print("\n" + report(results))


@pytest.mark.parametrize("scenario", DETECT, ids=lambda s: s.name)
def test_planted_threat_is_ranked(scenario):
    r = evaluate(scenario)
    assert r["rank"] is not None, f"{scenario.name}: planted endpoint absent from ranking"
    assert r["rank"] < 3, (
        f"{scenario.name}: rank {r['rank'] + 1}/{r['total']} "
        f"(score {r['score']}, reasons {r['reasons']}) - {scenario.note}")


@pytest.mark.parametrize("scenario", REJECT, ids=lambda s: s.name)
def test_benign_traffic_is_not_ranked(scenario):
    r = evaluate(scenario)
    assert r["rank"] is not None, f"{scenario.name}: planted endpoint absent from ranking"
    assert r["rank"] >= 3, (
        f"{scenario.name}: false positive at rank {r['rank'] + 1}/{r['total']} "
        f"(score {r['score']}, reasons {r['reasons']}) - {scenario.note}")
