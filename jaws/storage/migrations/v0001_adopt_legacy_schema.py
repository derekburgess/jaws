"""Migration 1: adopt the Benchmark 0 evidence schema without rewriting data."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=1,
    name="adopt_legacy_evidence_schema",
    statements=(
        CypherStatement(
            "protect one applied-migration record per version",
            "CREATE CONSTRAINT jaws_schema_migration_version_unique IF NOT EXISTS "
            "FOR (m:JAWS_SCHEMA_MIGRATION) REQUIRE m.VERSION IS UNIQUE",
        ),
        CypherStatement(
            "preserve capture identity uniqueness",
            "CREATE CONSTRAINT capture_id_unique IF NOT EXISTS "
            "FOR (c:CAPTURE) REQUIRE c.CAPTURE_ID IS UNIQUE",
        ),
        CypherStatement(
            "preserve IP address identity uniqueness",
            "CREATE CONSTRAINT ip_address_unique IF NOT EXISTS "
            "FOR (ip:IP_ADDRESS) REQUIRE ip.IP_ADDRESS IS UNIQUE",
        ),
        CypherStatement(
            "preserve organization identity uniqueness",
            "CREATE CONSTRAINT organization_unique IF NOT EXISTS "
            "FOR (org:ORGANIZATION) REQUIRE org.ORGANIZATION IS UNIQUE",
        ),
        CypherStatement(
            "preserve endpoint capture-scope lookup",
            "CREATE INDEX endpoint_capture_index IF NOT EXISTS "
            "FOR (e:ENDPOINT) ON (e.IP_ADDRESS, e.CAPTURE_ID)",
        ),
        CypherStatement(
            "preserve endpoint IP lookup",
            "CREATE INDEX endpoint_ip_index IF NOT EXISTS FOR (e:ENDPOINT) ON (e.IP_ADDRESS)",
        ),
        CypherStatement(
            "preserve packet capture-scope lookup",
            "CREATE INDEX packet_capture_index IF NOT EXISTS FOR (p:PACKET) ON (p.CAPTURE_ID)",
        ),
        CypherStatement(
            "preserve packet timestamp lookup",
            "CREATE INDEX packet_timestamp_index IF NOT EXISTS FOR (p:PACKET) ON (p.TIMESTAMP)",
        ),
        CypherStatement(
            "preserve IP-scoped port lookup",
            "CREATE INDEX port_composite_index IF NOT EXISTS "
            "FOR (p:PORT) ON (p.PORT, p.IP_ADDRESS)",
        ),
    ),
    required_schema=(
        SchemaObject("constraint", "capture_id_unique", "CAPTURE", ("CAPTURE_ID",)),
        SchemaObject("constraint", "ip_address_unique", "IP_ADDRESS", ("IP_ADDRESS",)),
        SchemaObject(
            "constraint",
            "jaws_schema_migration_version_unique",
            "JAWS_SCHEMA_MIGRATION",
            ("VERSION",),
        ),
        SchemaObject("constraint", "organization_unique", "ORGANIZATION", ("ORGANIZATION",)),
        SchemaObject("index", "endpoint_capture_index", "ENDPOINT", ("IP_ADDRESS", "CAPTURE_ID")),
        SchemaObject("index", "endpoint_ip_index", "ENDPOINT", ("IP_ADDRESS",)),
        SchemaObject("index", "packet_capture_index", "PACKET", ("CAPTURE_ID",)),
        SchemaObject("index", "packet_timestamp_index", "PACKET", ("TIMESTAMP",)),
        SchemaObject("index", "port_composite_index", "PORT", ("PORT", "IP_ADDRESS")),
    ),
    reversible=False,
    rollback=(
        "No automatic downgrade. A pre-existing graph cannot distinguish schema objects "
        "created by JAWS 2.0 from objects adopted by this migration. Restore a verified "
        "backup to reverse adoption."
    ),
)
