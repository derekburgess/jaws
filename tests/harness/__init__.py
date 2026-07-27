"""Synthetic-injection recall harness for JAWS detection.

Plants traffic with a KNOWN label into a background capture and measures whether the
detector ranks it. Everything is built as the packet DataFrame jaws_compute already
consumes, so the real profile-building and scoring code runs — including the timing
derivation, which is where both shipped detector bugs actually lived.

Two tiers of ground truth:
  - synthetic generators (scenarios.py) — fast, fully controlled, but they encode OUR
    model of a beacon, so passing them is necessary and not sufficient;
  - real captures (pcap.py) — adversary-authored timing, labels from published IOCs,
    opt-in via JAWS_PCAP_DIR.

Nothing here emits traffic. A "packet" is a dict.
"""
from .scenarios import HOST, SCENARIOS, Scenario, background_packets
from .recall import evaluate, report
from .pcap import pcap_scenarios, NJRAT

__all__ = ["HOST", "SCENARIOS", "Scenario", "background_packets", "evaluate", "report",
           "pcap_scenarios", "NJRAT", "all_scenarios"]


def all_scenarios():
    """Synthetic scenarios, plus real-capture ones when JAWS_PCAP_DIR is populated."""
    return SCENARIOS + pcap_scenarios()
