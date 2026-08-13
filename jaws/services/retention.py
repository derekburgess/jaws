"""Plan and apply explicit retention without database or interface dependencies."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import (
    AuditContext,
    AuditEvent,
    AuditEventId,
    AuditOperation,
    Clock,
    IdGenerator,
    ProfileScopeSummary,
    ProfileStatus,
    RetentionMode,
    RetentionPlan,
    RetentionPolicy,
    RetentionResource,
    RetentionResult,
    canonical_digest,
)
from jaws.ports import ProfileRepository, RetentionConflictError


class UnsupportedRetentionPolicyError(ValueError):
    """A declared rule has no safe implementation in the current milestone."""


@dataclass(frozen=True, slots=True)
class RetentionService:
    profiles: ProfileRepository
    clock: Clock
    event_ids: IdGenerator[AuditEventId]
    audit_context: AuditContext

    @staticmethod
    def _validate(policy: RetentionPolicy) -> None:
        unsupported = tuple(
            rule.resource
            for rule in policy.rules
            if rule.resource is not RetentionResource.PROFILE_SETS
            and rule.mode is not RetentionMode.KEEP_ALL
        )
        if unsupported:
            raise UnsupportedRetentionPolicyError(
                "retention deletion is not yet implemented for: "
                + ", ".join(resource.value for resource in unsupported)
            )

    def plan(self, policy: RetentionPolicy) -> RetentionPlan:
        """Return the complete deletion plan without performing any writes."""

        self._validate(policy)
        summaries = self.profiles.list_scopes()
        protected = tuple(
            summary for summary in summaries if summary.status is ProfileStatus.LEGACY_QUARANTINED
        )
        eligible = tuple(
            summary
            for summary in summaries
            if summary.status is not ProfileStatus.LEGACY_QUARANTINED
        )
        rule = policy.rule(RetentionResource.PROFILE_SETS)
        if rule.mode is RetentionMode.KEEP_ALL:
            retained = eligible
            deleted: tuple[ProfileScopeSummary, ...] = ()
        else:
            assert rule.keep_latest is not None
            retained = eligible[: rule.keep_latest]
            deleted = eligible[rule.keep_latest :]
        return RetentionPlan(
            policy=policy,
            retained_profile_scopes=retained,
            deleted_profile_scopes=deleted,
            protected_profile_scopes=protected,
        )

    def apply(self, plan: RetentionPlan) -> RetentionResult:
        """Apply an unchanged dry-run plan or fail without deleting partial data."""

        current = self.plan(plan.policy)
        if current != plan:
            raise RetentionConflictError("retained data changed after retention was planned")
        audit_event = AuditEvent(
            event_id=self.event_ids.new(),
            operation=AuditOperation.RETENTION_APPLY,
            context=self.audit_context,
            occurred_at=self.clock.now(),
            plan_digest=canonical_digest(plan),
            affected_records=plan.profile_records_to_delete,
        )
        deleted = self.profiles.delete_scopes(
            plan.deleted_profile_scopes,
            audit_event=audit_event,
        )
        return RetentionResult(
            plan=plan,
            applied=True,
            deleted_profile_records=deleted,
            audit_event=audit_event,
        )

    def dry_run(self, policy: RetentionPolicy) -> RetentionResult:
        return RetentionResult(plan=self.plan(policy), applied=False)
