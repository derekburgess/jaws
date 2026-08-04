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

import os
import tempfile

# Importing the legacy finder pulls in Matplotlib even when the harness does not plot.
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "jaws-matplotlib"))

from .pcap import NJRAT, documented_pcap_scenarios, pcap_scenarios
from .recall import evaluate, report
from .scenarios import HOST, SCENARIOS, Scenario, background_packets

__all__ = [
    "HOST",
    "SCENARIOS",
    "Scenario",
    "background_packets",
    "evaluate",
    "report",
    "documented_pcap_scenarios",
    "pcap_scenarios",
    "NJRAT",
    "all_scenarios",
    "recall_source",
]

RECALL_SOURCES = {"all", "synthetic", "pcap"}


def recall_source():
    """Return the requested benchmark source, rejecting silent misspellings."""
    source = os.environ.get("JAWS_RECALL_SOURCE", "all").strip().lower()
    if source not in RECALL_SOURCES:
        choices = ", ".join(sorted(RECALL_SOURCES))
        raise ValueError(f"JAWS_RECALL_SOURCE must be one of {choices}; received {source!r}")
    return source


def all_scenarios(source=None):
    """Return scenarios from the requested source.

    ``all`` preserves the original harness behavior. Explicit ``synthetic`` and
    ``pcap`` modes let the two quality tiers run independently even when a local
    PCAP directory is configured.
    """
    source = recall_source() if source is None else source.strip().lower()
    if source not in RECALL_SOURCES:
        choices = ", ".join(sorted(RECALL_SOURCES))
        raise ValueError(f"source must be one of {choices}; received {source!r}")
    if source == "synthetic":
        return list(SCENARIOS)
    if source == "pcap":
        return pcap_scenarios()
    return list(SCENARIOS) + pcap_scenarios()
