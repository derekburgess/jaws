"""Migration 4: durable, queryable audit identity for destructive operations."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=4,
    name="administration_audit_log",
    statements=(
        CypherStatement(
            "protect audit event identity",
            "CREATE CONSTRAINT jaws_audit_event_id_unique IF NOT EXISTS "
            "FOR (event:JAWS_AUDIT_EVENT) REQUIRE event.EVENT_ID IS UNIQUE",
        ),
        CypherStatement(
            "index audit event time",
            "CREATE INDEX jaws_audit_occurred_at_index IF NOT EXISTS "
            "FOR (event:JAWS_AUDIT_EVENT) ON (event.OCCURRED_AT)",
        ),
        CypherStatement(
            "index audit operation and target",
            "CREATE INDEX jaws_audit_operation_target_index IF NOT EXISTS "
            "FOR (event:JAWS_AUDIT_EVENT) ON (event.OPERATION, event.TARGET_DATABASE)",
        ),
    ),
    required_schema=(
        SchemaObject("constraint", "jaws_audit_event_id_unique", "JAWS_AUDIT_EVENT", ("EVENT_ID",)),
        SchemaObject("index", "jaws_audit_occurred_at_index", "JAWS_AUDIT_EVENT", ("OCCURRED_AT",)),
        SchemaObject(
            "index",
            "jaws_audit_operation_target_index",
            "JAWS_AUDIT_EVENT",
            ("OPERATION", "TARGET_DATABASE"),
        ),
    ),
    reversible=True,
    rollback=(
        "Export or retain JAWS_AUDIT_EVENT nodes, then drop the version-4 constraint and indexes. "
        "The migration creates no audit records and never changes managed evidence."
    ),
)
