"""Migration 3: explicit enrichment provenance and legacy profile scope semantics."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=3,
    name="enrichment_provenance_and_profile_scopes",
    statements=(
        CypherStatement(
            "protect researcher annotation identity",
            "CREATE CONSTRAINT entity_annotation_key_unique IF NOT EXISTS "
            "FOR (annotation:ENTITY_ANNOTATION) REQUIRE annotation.ANNOTATION_KEY IS UNIQUE",
        ),
        CypherStatement(
            "index provider enrichment state",
            "CREATE INDEX ip_enrichment_status_index IF NOT EXISTS "
            "FOR (address:IP_ADDRESS) ON (address.ENRICHMENT_STATUS)",
        ),
        CypherStatement(
            "index profile scope and computation time",
            "CREATE INDEX endpoint_scope_computed_index IF NOT EXISTS "
            "FOR (endpoint:ENDPOINT) ON (endpoint.SCOPE_ID, endpoint.TIMESTAMP)",
        ),
        CypherStatement(
            "materialize the explicit pooled all-evidence scope",
            """
            MERGE (scope:OBSERVATION_SCOPE {SCOPE_ID: 'scope_pooled_all'})
            ON CREATE SET scope.KIND = 'pooled',
                          scope.CREATED_AT = datetime(),
                          scope.MIGRATED_FROM_SCHEMA_VERSION = 3
            """,
        ),
        CypherStatement(
            "materialize a quarantine scope for unstamped legacy profiles",
            """
            MERGE (scope:OBSERVATION_SCOPE {SCOPE_ID: 'scope_legacy_unstamped'})
            ON CREATE SET scope.KIND = 'legacy',
                          scope.CREATED_AT = datetime(),
                          scope.MIGRATED_FROM_SCHEMA_VERSION = 3,
                          scope.QUARANTINED = true
            """,
        ),
        CypherStatement(
            "move pooled legacy profiles to the explicit pooled scope",
            """
            MATCH (endpoint:ENDPOINT {CAPTURE_ID: 'all'})
            SET endpoint.V3_PREVIOUS_SCOPE_ID = coalesce(
                    endpoint.V3_PREVIOUS_SCOPE_ID, endpoint.SCOPE_ID
                ),
                endpoint.SCOPE_ID = 'scope_pooled_all',
                endpoint.PROFILE_STATUS = coalesce(
                    endpoint.PROFILE_STATUS, 'legacy_unversioned'
                ),
                endpoint.OUTLIER_STATUS = CASE
                    WHEN endpoint.OUTLIER IS NULL THEN 'not_scored'
                    WHEN endpoint.OUTLIER THEN 'outlier'
                    ELSE 'inlier'
                END,
                endpoint.MIGRATED_FROM_SCHEMA_VERSION = 3
            """,
        ),
        CypherStatement(
            "quarantine rather than delete unstamped legacy profiles",
            """
            MATCH (endpoint:ENDPOINT)
            WHERE endpoint.CAPTURE_ID IS NULL
            SET endpoint.V3_PREVIOUS_SCOPE_ID = coalesce(
                    endpoint.V3_PREVIOUS_SCOPE_ID, endpoint.SCOPE_ID
                ),
                endpoint.SCOPE_ID = 'scope_legacy_unstamped',
                endpoint.PROFILE_STATUS = 'legacy_quarantined',
                endpoint.OUTLIER_STATUS = CASE
                    WHEN endpoint.OUTLIER IS NULL THEN 'not_scored'
                    WHEN endpoint.OUTLIER THEN 'outlier'
                    ELSE 'inlier'
                END,
                endpoint.MIGRATED_FROM_SCHEMA_VERSION = 3
            """,
        ),
        CypherStatement(
            "mark capture-scoped legacy profiles without inventing representation provenance",
            """
            MATCH (endpoint:ENDPOINT)
            WHERE endpoint.CAPTURE_ID IS NOT NULL AND endpoint.CAPTURE_ID <> 'all'
            SET endpoint.PROFILE_STATUS = coalesce(
                    endpoint.PROFILE_STATUS, 'legacy_unversioned'
                ),
                endpoint.OUTLIER_STATUS = CASE
                    WHEN endpoint.OUTLIER IS NULL THEN 'not_scored'
                    WHEN endpoint.OUTLIER THEN 'outlier'
                    ELSE 'inlier'
                END,
                endpoint.MIGRATED_FROM_SCHEMA_VERSION = coalesce(
                    endpoint.MIGRATED_FROM_SCHEMA_VERSION, 3
                )
            """,
        ),
    ),
    required_schema=(
        SchemaObject(
            "constraint", "entity_annotation_key_unique", "ENTITY_ANNOTATION", ("ANNOTATION_KEY",)
        ),
        SchemaObject("index", "ip_enrichment_status_index", "IP_ADDRESS", ("ENRICHMENT_STATUS",)),
        SchemaObject(
            "index",
            "endpoint_scope_computed_index",
            "ENDPOINT",
            ("SCOPE_ID", "TIMESTAMP"),
        ),
    ),
    reversible=True,
    rollback=(
        "Conditionally reversible before version-3 writers create enrichment, annotation, or "
        "versioned profile records: restore ENDPOINT.SCOPE_ID from V3_PREVIOUS_SCOPE_ID where "
        "MIGRATED_FROM_SCHEMA_VERSION=3; remove PROFILE_STATUS, OUTLIER_STATUS, migration marker, "
        "and V3_PREVIOUS_SCOPE_ID only from those migrated endpoints; delete version-3-marked "
        "pooled/quarantine scopes; and drop the three version-3 schema objects. Preserve all "
        "legacy ENDPOINT nodes and OUTLIER values. Once version-3 records exist, export and "
        "restore instead of applying the conditional rollback."
    ),
)
