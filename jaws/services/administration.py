"""Explicitly confirmed administration over a database-bound repository."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import (
    AdministrationPlan,
    AdministrationResult,
    AuditContext,
    AuditEvent,
    AuditEventId,
    AuditOperation,
    Clock,
    IdGenerator,
)
from jaws.ports import AdministrationConflictError, AdministrationRepository


class AdministrationConfirmationError(ValueError):
    """The caller did not repeat the exact operation, database, and plan digest."""


@dataclass(frozen=True, slots=True)
class AdministrationService:
    repository: AdministrationRepository
    clock: Clock
    event_ids: IdGenerator[AuditEventId]
    context: AuditContext

    def plan(self) -> AdministrationPlan:
        plan = self.repository.plan()
        if plan.target_database != self.context.target_database:
            raise AdministrationConflictError(
                "administration repository target does not match its audit context"
            )
        return plan

    def erase(self, plan: AdministrationPlan, confirmation: str) -> AdministrationResult:
        if confirmation != plan.confirmation:
            raise AdministrationConfirmationError(
                "confirmation must exactly match the operation, database, and current plan digest"
            )
        if plan.target_database != self.context.target_database:
            raise AdministrationConfirmationError("confirmation targets a different database")
        event = AuditEvent(
            event_id=self.event_ids.new(),
            operation=AuditOperation.ERASE_MANAGED_EVIDENCE,
            context=self.context,
            occurred_at=self.clock.now(),
            plan_digest=plan.digest,
            affected_records=plan.node_count,
        )
        deleted_nodes, deleted_relationships = self.repository.erase(plan, event)
        return AdministrationResult(plan, event, deleted_nodes, deleted_relationships)

    def audit_events(self, *, limit: int = 100) -> tuple[AuditEvent, ...]:
        return self.repository.audit_events(limit=limit)
