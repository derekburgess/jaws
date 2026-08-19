"""Migration 5: minimal experiment/run discovery index schema."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=5,
    name="experiment_run_index",
    statements=(
        CypherStatement(
            "protect experiment identity",
            "CREATE CONSTRAINT jaws_experiment_id_unique IF NOT EXISTS "
            "FOR (experiment:JAWS_EXPERIMENT) REQUIRE experiment.EXPERIMENT_ID IS UNIQUE",
        ),
        CypherStatement(
            "protect experiment run identity",
            "CREATE CONSTRAINT jaws_experiment_run_id_unique IF NOT EXISTS "
            "FOR (run:JAWS_EXPERIMENT_RUN) REQUIRE run.RUN_ID IS UNIQUE",
        ),
        CypherStatement(
            "index experiment run lifecycle and creation time",
            "CREATE INDEX jaws_experiment_run_state_created_index IF NOT EXISTS "
            "FOR (run:JAWS_EXPERIMENT_RUN) ON (run.STATE, run.CREATED_AT)",
        ),
        CypherStatement(
            "index canonical artifact locations",
            "CREATE INDEX jaws_experiment_run_artifact_uri_index IF NOT EXISTS "
            "FOR (run:JAWS_EXPERIMENT_RUN) ON (run.ARTIFACT_URI)",
        ),
    ),
    required_schema=(
        SchemaObject(
            "constraint",
            "jaws_experiment_id_unique",
            "JAWS_EXPERIMENT",
            ("EXPERIMENT_ID",),
        ),
        SchemaObject(
            "constraint",
            "jaws_experiment_run_id_unique",
            "JAWS_EXPERIMENT_RUN",
            ("RUN_ID",),
        ),
        SchemaObject(
            "index",
            "jaws_experiment_run_state_created_index",
            "JAWS_EXPERIMENT_RUN",
            ("STATE", "CREATED_AT"),
        ),
        SchemaObject(
            "index",
            "jaws_experiment_run_artifact_uri_index",
            "JAWS_EXPERIMENT_RUN",
            ("ARTIFACT_URI",),
        ),
    ),
    reversible=True,
    rollback=(
        "Export experiment/run index nodes and their RUN_OF and SUPERSEDES relationships, then "
        "drop the version-5 constraints and indexes. Canonical experiment artifacts remain "
        "authoritative outside Neo4j."
    ),
)
