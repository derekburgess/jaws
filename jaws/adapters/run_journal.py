"""Atomic local lifecycle snapshots and cooperative cancellation markers."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jaws.domain import ExperimentRun, RunId, canonical_json


class RunJournal:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runs = self.root / "runs"
        self.cancellations = self.root / "cancellations"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.cancellations.mkdir(parents=True, exist_ok=True)

    def write(self, run: ExperimentRun) -> None:
        assert run.run_id is not None
        destination = self.runs / f"{run.run_id.value}.json"
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(canonical_json(run) + "\n", encoding="utf-8")
        os.replace(temporary, destination)

    def read(self, run_id: RunId) -> Mapping[str, Any] | None:
        path = self.runs / f"{run_id.value}.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid run journal snapshot: {path}")
        return value

    def request_cancellation(self, run_id: RunId) -> Path:
        marker = self.cancellations / f"{run_id.value}.cancel"
        marker.touch(exist_ok=True)
        return marker

    def cancellation_signal(self, run_id: RunId) -> FileCancellationSignal:
        return FileCancellationSignal(self.cancellations / f"{run_id.value}.cancel")


class FileCancellationSignal:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def is_cancelled(self) -> bool:
        return self.marker.exists()
