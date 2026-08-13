"""Shared Neo4j serialization for payload-free destructive-operation audit events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from jaws.domain import (
    AuditContext,
    AuditEvent,
    AuditEventId,
    AuditOperation,
    CanonicalDigest,
    utc_text,
)


class Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


CREATE_AUDIT_EVENT_QUERY = """
CREATE (:JAWS_AUDIT_EVENT {
    EVENT_ID: $event_id,
    OPERATION: $operation,
    TARGET_DATABASE: $target_database,
    OCCURRED_AT: datetime($occurred_at),
    ACTOR: $actor,
    INTERFACE: $interface,
    PLAN_DIGEST: $plan_digest,
    AFFECTED_RECORDS: $affected_records
})
"""

LIST_AUDIT_EVENTS_QUERY = """
MATCH (event:JAWS_AUDIT_EVENT)
RETURN event.EVENT_ID AS event_id,
       event.OPERATION AS operation,
       event.TARGET_DATABASE AS target_database,
       toString(event.OCCURRED_AT) AS occurred_at,
       event.ACTOR AS actor,
       event.INTERFACE AS interface,
       event.PLAN_DIGEST AS plan_digest,
       event.AFFECTED_RECORDS AS affected_records
ORDER BY event.OCCURRED_AT DESC, event.EVENT_ID DESC
LIMIT $limit
"""


def audit_parameters(event: AuditEvent) -> Mapping[str, object]:
    return {
        "event_id": event.event_id.value,
        "operation": event.operation.value,
        "target_database": event.context.target_database,
        "occurred_at": utc_text(event.occurred_at),
        "actor": event.context.actor,
        "interface": event.context.interface,
        "plan_digest": str(event.plan_digest),
        "affected_records": event.affected_records,
    }


def audit_event(row: Record) -> AuditEvent:
    from datetime import datetime

    return AuditEvent(
        event_id=AuditEventId(str(row["event_id"])),
        operation=AuditOperation(str(row["operation"])),
        context=AuditContext(
            actor=str(row["actor"]),
            interface=str(row["interface"]),
            target_database=str(row["target_database"]),
        ),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"]).replace("Z", "+00:00")),
        plan_digest=CanonicalDigest(str(row["plan_digest"])),
        affected_records=int(row["affected_records"]),
    )
