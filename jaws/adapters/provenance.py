"""Allowlisted runtime provenance collection for reproducible experiment bundles."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from jaws.domain import (
    CanonicalDigest,
    DependencyVersion,
    ExperimentSpec,
    ProvenanceRecord,
    ResourceUsage,
    StrategyProvenance,
)


class ProvenanceCollector:
    """Collect output-relevant system facts without serializing the environment."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository.resolve()

    def collect(
        self,
        specification: ExperimentSpec,
        *,
        evidence_digests: Mapping[str, CanonicalDigest] | None = None,
        usage: ResourceUsage | None = None,
    ) -> ProvenanceRecord:
        assert specification.representation is not None
        assert specification.ranker is not None
        memory: int | None = None
        try:
            import psutil  # type: ignore[import-untyped]

            memory = int(psutil.virtual_memory().total)
        except (ImportError, OSError):
            pass
        strategies = (
            StrategyProvenance(
                "representation",
                specification.representation.representation_id,
                specification.representation.version,
                specification.representation.schema_version,
            ),
            StrategyProvenance(
                "reference",
                specification.reference.kind.value,
                specification.reference.version,
                specification.reference.schema_version,
            ),
            StrategyProvenance(
                "ranker",
                specification.ranker.ranker_id,
                specification.ranker.version,
                specification.ranker.schema_version,
                model=self._optional_parameter(specification.ranker.parameters, "model"),
                provider=self._optional_parameter(specification.ranker.parameters, "provider"),
            ),
            StrategyProvenance(
                "evaluator",
                specification.evaluator.component_id,
                specification.evaluator.version,
                specification.evaluator.schema_version,
            ),
            StrategyProvenance(
                "renderer",
                specification.renderer.component_id,
                specification.renderer.version,
                specification.renderer.schema_version,
            ),
        )
        container_digests = tuple(
            CanonicalDigest(value)
            for name in ("JAWS_CONTAINER_DIGEST", "CONTAINER_IMAGE_DIGEST")
            if (value := os.environ.get(name)) is not None
        )
        return ProvenanceRecord(
            code_commit=self._git("rev-parse", "HEAD") or "unknown",
            dirty_tree=bool(self._git("status", "--porcelain")),
            package_version=self._package_version(),
            python_version=platform.python_version(),
            os=f"{platform.system()} {platform.release()}",
            architecture=platform.machine() or "unknown",
            cpu=platform.processor() or platform.machine() or "unknown",
            gpu=os.environ.get("JAWS_GPU_MODEL"),
            memory_bytes=memory,
            container_digests=container_digests,
            dependencies=tuple(
                DependencyVersion(distribution.metadata["Name"], distribution.version)
                for distribution in importlib.metadata.distributions()
                if distribution.metadata["Name"]
            ),
            schema_versions={
                "experiment": str(specification.schema_version),
                "hypothesis": str(
                    getattr(specification.hypothesis, "schema_version", specification.schema_version)
                ),
            },
            evidence_digests=evidence_digests or {},
            label_source_versions=specification.label_source_versions,
            strategies=strategies,
            seed=specification.ranker.seed,
            deterministic_settings=specification.deterministic_settings,
            usage=usage or ResourceUsage(),
        )

    def _git(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                ("git", *arguments),
                cwd=self.repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _package_version() -> str:
        try:
            return importlib.metadata.version("JAWS")
        except importlib.metadata.PackageNotFoundError:
            return "unknown"

    @staticmethod
    def _optional_parameter(parameters: Mapping[str, object], key: str) -> str | None:
        value = parameters.get(key)
        return str(value) if value is not None else None


def interpreter_identity() -> str:
    """Small diagnostic helper retained for CLI summaries."""

    return f"{sys.implementation.name}-{platform.python_version()}"
