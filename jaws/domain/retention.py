"""Declared retention policies, dry-run plans, and apply results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .administration import AuditEvent
from .profiles import ProfileScopeSummary


class RetentionResource(StrEnum):
    RAW_PACKETS = "raw_packets"
    CAPTURE_METADATA = "capture_metadata"
    PROFILE_SETS = "profile_sets"
    EXPERIMENT_INDEXES = "experiment_indexes"
    ARTIFACT_BUNDLES = "artifact_bundles"


class RetentionMode(StrEnum):
    KEEP_ALL = "keep_all"
    KEEP_LATEST = "keep_latest"


@dataclass(frozen=True, slots=True)
class RetentionRule:
    resource: RetentionResource
    mode: RetentionMode = RetentionMode.KEEP_ALL
    keep_latest: int | None = None

    def __post_init__(self) -> None:
        if self.mode is RetentionMode.KEEP_ALL and self.keep_latest is not None:
            raise ValueError("keep-all retention cannot declare a latest-item limit")
        if self.mode is RetentionMode.KEEP_LATEST:
            if self.keep_latest is None or self.keep_latest < 1:
                raise ValueError("keep-latest retention requires a positive item limit")


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """One independently declared rule for every retained resource class."""

    policy_id: str
    version: str
    rules: tuple[RetentionRule, ...]

    def __post_init__(self) -> None:
        policy_id = self.policy_id.strip()
        version = self.version.strip()
        if not policy_id or not version:
            raise ValueError("retention policy ID and version cannot be empty")
        resources = tuple(rule.resource for rule in self.rules)
        expected = set(RetentionResource)
        if len(resources) != len(set(resources)):
            raise ValueError("retention policy resources must be unique")
        if set(resources) != expected:
            missing = sorted(resource.value for resource in expected - set(resources))
            raise ValueError(
                "retention policy must declare every resource"
                + (f"; missing: {', '.join(missing)}" if missing else "")
            )
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self, "rules", tuple(sorted(self.rules, key=lambda rule: rule.resource.value))
        )

    def rule(self, resource: RetentionResource) -> RetentionRule:
        return next(rule for rule in self.rules if rule.resource is resource)

    @classmethod
    def legacy_profile_limit(cls, retain: int | None) -> RetentionPolicy:
        """Translate the 2.0 compute flag into a complete explicit resource policy."""

        if retain is not None and retain < 0:
            raise ValueError("profile retention cannot be negative")
        profile_rule = (
            RetentionRule(RetentionResource.PROFILE_SETS)
            if not retain
            else RetentionRule(
                RetentionResource.PROFILE_SETS,
                RetentionMode.KEEP_LATEST,
                retain,
            )
        )
        return cls(
            policy_id="legacy_compute_profile_retention",
            version="1",
            rules=tuple(
                profile_rule
                if resource is RetentionResource.PROFILE_SETS
                else RetentionRule(resource)
                for resource in RetentionResource
            ),
        )


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """Deterministic dry-run result; creating it never mutates retained data."""

    policy: RetentionPolicy
    retained_profile_scopes: tuple[ProfileScopeSummary, ...]
    deleted_profile_scopes: tuple[ProfileScopeSummary, ...]
    protected_profile_scopes: tuple[ProfileScopeSummary, ...] = ()

    @property
    def profile_records_to_delete(self) -> int:
        return sum(summary.profile_count for summary in self.deleted_profile_scopes)

    @property
    def has_deletions(self) -> bool:
        return bool(self.deleted_profile_scopes)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    plan: RetentionPlan
    applied: bool
    deleted_profile_records: int = 0
    audit_event: AuditEvent | None = None

    def __post_init__(self) -> None:
        if self.deleted_profile_records < 0:
            raise ValueError("deleted profile count cannot be negative")
        expected = self.plan.profile_records_to_delete
        if self.applied and self.deleted_profile_records != expected:
            raise ValueError("applied retention result must match its planned deletion count")
        if self.applied and self.audit_event is None:
            raise ValueError("applied retention result requires an audit event")
        if not self.applied and self.deleted_profile_records:
            raise ValueError("dry-run retention cannot report deleted records")
        if not self.applied and self.audit_event is not None:
            raise ValueError("dry-run retention cannot create an audit event")
