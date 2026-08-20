"""Atomic, portable local experiment bundles and offline verification."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, cast

from jaws.domain import (
    CanonicalDigest,
    ExperimentRun,
    ExperimentSpec,
    RunId,
    canonical_json,
    primitive,
)

BUNDLE_FORMAT = "jaws-experiment-bundle"
BUNDLE_VERSION = "1.0.0"
MANIFEST_PATH = "manifest.json"
RAW_CAPTURE_SUFFIXES = frozenset({".pcap", ".pcapng", ".cap"})


def _digest(content: bytes) -> CanonicalDigest:
    return CanonicalDigest(hashlib.sha256(content).hexdigest())


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"bundle path is not bounded: {value}")
    return path


def _json_bytes(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


@dataclass(frozen=True, slots=True)
class BundleVerification:
    valid: bool
    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BundleInspection:
    path: Path
    manifest: Mapping[str, Any]
    specification: Mapping[str, Any]
    run: Mapping[str, Any]
    verification: BundleVerification


@dataclass(frozen=True, slots=True)
class GarbageCollectionPlan:
    removable: tuple[Path, ...]
    protected: tuple[Path, ...]
    removed: tuple[Path, ...] = ()


class ExperimentBundleStore:
    """Canonical filesystem store with staged atomic publication."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def bundle_path(self, experiment_id: str, run_id: str) -> Path:
        return self.root / "experiments" / experiment_id / "runs" / run_id

    def write(
        self,
        specification: ExperimentSpec,
        run: ExperimentRun,
        artifacts: Mapping[str, bytes],
        *,
        referenced_run_ids: Iterable[RunId] = (),
    ) -> tuple[Path, CanonicalDigest]:
        if run.experiment_id != specification.experiment_id:
            raise ValueError("run and specification experiment identities differ")
        if run.specification_digest != specification.digest:
            raise ValueError("run specification digest is invalid")
        assert run.run_id is not None
        destination = self.bundle_path(run.experiment_id.value, run.run_id.value)
        if destination.exists():
            raise FileExistsError(f"immutable experiment bundle already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(mkdtemp(prefix=f".{run.run_id.value}-", dir=destination.parent))
        try:
            raw_paths = tuple(path for path in artifacts if self._is_raw_capture(Path(path)))
            if raw_paths:
                raise ValueError(
                    "experiment bundles may reference but never embed raw captures: "
                    + ", ".join(raw_paths)
                )
            payloads: dict[str, bytes] = {
                "experiment/spec.json": _json_bytes(specification),
                "run/run.json": _json_bytes(run),
                **{str(_safe_relative(path)): content for path, content in artifacts.items()},
            }
            checksums: dict[str, str] = {}
            for logical_path, content in sorted(payloads.items()):
                path = staging / _safe_relative(logical_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                checksums[logical_path] = str(_digest(content))
            manifest: dict[str, Any] = {
                "format": BUNDLE_FORMAT,
                "version": BUNDLE_VERSION,
                "experiment_id": run.experiment_id.value,
                "run_id": run.run_id.value,
                "state": run.state.value,
                "specification_digest": str(specification.digest),
                "referenced_run_ids": sorted(str(item) for item in referenced_run_ids),
                "artifacts": checksums,
                "raw_capture_redistributed": False,
            }
            manifest_bytes = _json_bytes(manifest)
            (staging / MANIFEST_PATH).write_bytes(manifest_bytes)
            verification = self.verify(staging)
            if not verification.valid:
                raise OSError(f"staged bundle verification failed: {verification}")
            os.replace(staging, destination)
            return destination, _digest(manifest_bytes)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def verify(self, bundle: Path) -> BundleVerification:
        path = bundle.resolve()
        manifest_path = path / MANIFEST_PATH
        if not manifest_path.is_file():
            return BundleVerification(False, missing=(MANIFEST_PATH,))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return BundleVerification(False, mismatched=(MANIFEST_PATH,))
        if manifest.get("format") != BUNDLE_FORMAT or manifest.get("version") != BUNDLE_VERSION:
            return BundleVerification(False, mismatched=(MANIFEST_PATH,))
        expected = manifest.get("artifacts")
        if not isinstance(expected, dict):
            return BundleVerification(False, mismatched=(MANIFEST_PATH,))
        missing: list[str] = []
        mismatched: list[str] = []
        for logical_path, digest in sorted(expected.items()):
            try:
                artifact = path / _safe_relative(str(logical_path))
            except ValueError:
                mismatched.append(str(logical_path))
                continue
            if not artifact.is_file():
                missing.append(str(logical_path))
            elif str(_digest(artifact.read_bytes())) != digest:
                mismatched.append(str(logical_path))
        actual = {
            item.relative_to(path).as_posix()
            for item in path.rglob("*")
            if item.is_file() and item != manifest_path
        }
        unexpected = sorted(actual - set(expected))
        return BundleVerification(
            not missing and not mismatched and not unexpected,
            tuple(missing),
            tuple(mismatched),
            tuple(unexpected),
        )

    def inspect(self, bundle: Path) -> BundleInspection:
        verification = self.verify(bundle)
        path = bundle.resolve()
        return BundleInspection(
            path,
            self._read_json(path / MANIFEST_PATH),
            self._read_json(path / "experiment/spec.json"),
            self._read_json(path / "run/run.json"),
            verification,
        )

    def export_bundle(self, bundle: Path, archive: Path) -> CanonicalDigest:
        verification = self.verify(bundle)
        if not verification.valid:
            raise ValueError(f"cannot export invalid bundle: {verification}")
        archive = archive.resolve()
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_name(f".{archive.name}.tmp")
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as output:
            for item in sorted(bundle.rglob("*")):
                if not item.is_file():
                    continue
                relative = item.relative_to(bundle)
                if self._is_raw_capture(relative):
                    continue
                info = output.gettarinfo(str(item), arcname=relative.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                with item.open("rb") as source:
                    output.addfile(info, source)
        os.replace(temporary, archive)
        return _digest(archive.read_bytes())

    def import_bundle(self, archive: Path) -> Path:
        staging = Path(mkdtemp(prefix=".import-", dir=self.root))
        try:
            with tarfile.open(archive.resolve(), "r:gz") as source:
                members = source.getmembers()
                for member in members:
                    relative = _safe_relative(member.name)
                    if member.issym() or member.islnk() or self._is_raw_capture(relative):
                        raise ValueError(f"unsafe or raw-capture archive member: {member.name}")
                    if member.isdir():
                        continue
                    extracted = source.extractfile(member)
                    if extracted is None:
                        raise ValueError(f"unsupported archive member: {member.name}")
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(extracted.read())
            verification = self.verify(staging)
            if not verification.valid:
                raise ValueError(f"imported bundle is invalid: {verification}")
            manifest = self._read_json(staging / MANIFEST_PATH)
            destination = self.bundle_path(str(manifest["experiment_id"]), str(manifest["run_id"]))
            if destination.exists():
                raise FileExistsError(f"immutable experiment bundle already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, destination)
            return destination
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def garbage_collect(self, *, execute: bool = False) -> GarbageCollectionPlan:
        bundles = tuple(self.root.glob("experiments/*/runs/*"))
        references: set[str] = set()
        valid: dict[Path, Mapping[str, Any]] = {}
        for bundle in bundles:
            inspection = self.inspect(bundle)
            if inspection.verification.valid:
                valid[bundle] = inspection.manifest
                references.update(str(item) for item in inspection.manifest["referenced_run_ids"])
        removable = tuple(
            sorted(
                bundle
                for bundle, manifest in valid.items()
                if manifest["run_id"] not in references
                and manifest["state"] in {"failed", "cancelled", "superseded"}
            )
        )
        protected = tuple(sorted(set(bundles) - set(removable)))
        removed: list[Path] = []
        if execute:
            for bundle in removable:
                shutil.rmtree(bundle)
                removed.append(bundle)
        return GarbageCollectionPlan(removable, protected, tuple(removed))

    @staticmethod
    def _read_json(path: Path) -> Mapping[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object: {path}")
        return value

    @staticmethod
    def _is_raw_capture(path: Path) -> bool:
        return path.suffix.lower() in RAW_CAPTURE_SUFFIXES or "raw" in path.parts


def inspection_json(value: BundleInspection) -> Mapping[str, Any]:
    """Stable JSON-friendly view used by both CLI rendering modes."""

    return cast(Mapping[str, Any], primitive(value))
