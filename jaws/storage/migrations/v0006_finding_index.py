"""Migration 6: optional reconstructable ranked-finding discovery index."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=6,
    name="finding_index",
    statements=(
        CypherStatement(
            "protect finding index identity",
            "CREATE CONSTRAINT jaws_finding_index_id_unique IF NOT EXISTS "
            "FOR (finding:JAWS_FINDING_INDEX) REQUIRE finding.FINDING_ID IS UNIQUE",
        ),
        CypherStatement(
            "index findings by run and rank",
            "CREATE INDEX jaws_finding_run_rank_index IF NOT EXISTS "
            "FOR (finding:JAWS_FINDING_INDEX) ON (finding.RUN_ID, finding.RANK)",
        ),
        CypherStatement(
            "index findings by entity identity",
            "CREATE INDEX jaws_finding_entity_index IF NOT EXISTS "
            "FOR (finding:JAWS_FINDING_INDEX) ON (finding.ENTITY_ID)",
        ),
    ),
    required_schema=(
        SchemaObject(
            "constraint",
            "jaws_finding_index_id_unique",
            "JAWS_FINDING_INDEX",
            ("FINDING_ID",),
        ),
        SchemaObject(
            "index",
            "jaws_finding_run_rank_index",
            "JAWS_FINDING_INDEX",
            ("RUN_ID", "RANK"),
        ),
        SchemaObject(
            "index",
            "jaws_finding_entity_index",
            "JAWS_FINDING_INDEX",
            ("ENTITY_ID",),
        ),
    ),
    reversible=True,
    rollback=(
        "Delete the reconstructable JAWS_FINDING_INDEX nodes, then drop the version-6 "
        "constraint and indexes. Canonical run artifacts remain authoritative."
    ),
)
