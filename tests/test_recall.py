"""Detection benchmark: does JAWS rank planted traffic it should, and ignore what it shouldn't?

Deselected by default (`-m recall`) because this measures detector QUALITY rather than
correctness — a change that moves recall is a finding to look at, not necessarily a
regression to block a commit on.

    conda run -n jaws pytest -m recall -s

Real-capture scenarios are included automatically when JAWS_PCAP_DIR points at a
directory holding the samples named in harness/pcap.py; they are skipped otherwise, so
the suite still runs for anyone who has not downloaded them.
"""
import pytest

from harness import all_scenarios, evaluate, report

pytestmark = pytest.mark.recall

SCENARIOS = all_scenarios()
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
