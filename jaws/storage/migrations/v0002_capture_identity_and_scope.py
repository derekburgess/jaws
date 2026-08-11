"""Migration 2: additive capture lifecycle, provenance, and scope ownership."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=2,
    name="capture_identity_lifecycle_and_scope",
    statements=(
        CypherStatement(
            "protect explicit observation-scope identity",
            "CREATE CONSTRAINT observation_scope_id_unique IF NOT EXISTS "
            "FOR (s:OBSERVATION_SCOPE) REQUIRE s.SCOPE_ID IS UNIQUE",
        ),
        CypherStatement(
            "protect canonical endpoint profile identity when present",
            "CREATE CONSTRAINT endpoint_profile_key_unique IF NOT EXISTS "
            "FOR (e:ENDPOINT) REQUIRE e.PROFILE_KEY IS UNIQUE",
        ),
        CypherStatement(
            "index compatibility lookup by human-readable legacy capture ID",
            "CREATE INDEX capture_legacy_id_index IF NOT EXISTS "
            "FOR (c:CAPTURE) ON (c.LEGACY_CAPTURE_ID)",
        ),
        CypherStatement(
            "index capture lifecycle state",
            "CREATE INDEX capture_state_index IF NOT EXISTS FOR (c:CAPTURE) ON (c.STATE)",
        ),
        CypherStatement(
            "index endpoint profiles by explicit observation scope",
            "CREATE INDEX endpoint_scope_index IF NOT EXISTS FOR (e:ENDPOINT) ON (e.SCOPE_ID)",
        ),
        CypherStatement(
            "backfill additive lifecycle and provenance compatibility fields",
            """
            MATCH (capture:CAPTURE)
            WHERE capture.CAPTURE_ID IS NOT NULL
            SET capture.LEGACY_CAPTURE_ID = coalesce(
                    capture.LEGACY_CAPTURE_ID, capture.CAPTURE_ID
                ),
                capture.STATE = coalesce(
                    capture.STATE,
                    CASE WHEN capture.PACKETS IS NULL THEN 'partial' ELSE 'complete' END
                ),
                capture.SOURCE_KIND = coalesce(capture.SOURCE_KIND, 'legacy_unknown'),
                capture.SOURCE_NAME = coalesce(capture.SOURCE_NAME, capture.SOURCE, 'unknown'),
                capture.PACKET_COUNT = coalesce(capture.PACKET_COUNT, capture.PACKETS, 0),
                capture.REGISTERED_AT = coalesce(capture.REGISTERED_AT, capture.STARTED),
                capture.STARTED_AT = coalesce(capture.STARTED_AT, capture.STARTED),
                capture.MIGRATED_FROM_SCHEMA_VERSION = coalesce(
                    capture.MIGRATED_FROM_SCHEMA_VERSION, 2
                )
            """,
        ),
        CypherStatement(
            "materialize one explicit scope for each legacy capture",
            """
            MATCH (capture:CAPTURE)
            WHERE capture.CAPTURE_ID IS NOT NULL
            MERGE (scope:OBSERVATION_SCOPE {SCOPE_ID: 'scope_' + capture.CAPTURE_ID})
            ON CREATE SET scope.KIND = 'capture',
                          scope.CREATED_AT = coalesce(capture.STARTED, datetime()),
                          scope.MIGRATED_FROM_SCHEMA_VERSION = 2
            MERGE (scope)-[:INCLUDES]->(capture)
            """,
        ),
        CypherStatement(
            "attach legacy session-scoped profiles to their explicit scope identity",
            """
            MATCH (endpoint:ENDPOINT)
            WHERE endpoint.CAPTURE_ID IS NOT NULL AND endpoint.SCOPE_ID IS NULL
            SET endpoint.SCOPE_ID = 'scope_' + endpoint.CAPTURE_ID,
                endpoint.SCOPE_MIGRATED_FROM_SCHEMA_VERSION = 2
            """,
        ),
    ),
    required_schema=(
        SchemaObject(
            "constraint",
            "observation_scope_id_unique",
            "OBSERVATION_SCOPE",
            ("SCOPE_ID",),
        ),
        SchemaObject("constraint", "endpoint_profile_key_unique", "ENDPOINT", ("PROFILE_KEY",)),
        SchemaObject("index", "capture_legacy_id_index", "CAPTURE", ("LEGACY_CAPTURE_ID",)),
        SchemaObject("index", "capture_state_index", "CAPTURE", ("STATE",)),
        SchemaObject("index", "endpoint_scope_index", "ENDPOINT", ("SCOPE_ID",)),
    ),
    reversible=True,
    rollback=(
        "Conditionally reversible before version-2 writers create new records: drop the "
        "three version-2 indexes/constraints, delete OBSERVATION_SCOPE nodes marked "
        "MIGRATED_FROM_SCHEMA_VERSION=2, and remove only capture/endpoint properties "
        "bearing the corresponding migration marker. Preserve every legacy CAPTURE_ID, "
        "SOURCE, STARTED, PACKETS, and ENDPOINT.CAPTURE_ID value. Once version-2 records "
        "exist, export and restore rather than applying this conditional rollback."
    ),
)
