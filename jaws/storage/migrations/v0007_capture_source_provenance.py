"""Migration 7: index descriptive PCAP source provenance by original filename."""

from .models import CypherStatement, Migration, SchemaObject

MIGRATION = Migration(
    version=7,
    name="capture_source_provenance",
    statements=(
        CypherStatement(
            "index captures by descriptive source filename",
            "CREATE INDEX capture_source_file_name_index IF NOT EXISTS "
            "FOR (capture:CAPTURE) ON (capture.SOURCE_FILE_NAME)",
        ),
    ),
    required_schema=(
        SchemaObject(
            "index",
            "capture_source_file_name_index",
            "CAPTURE",
            ("SOURCE_FILE_NAME",),
        ),
    ),
    reversible=True,
    rollback=(
        "Drop capture_source_file_name_index. Descriptive source properties may remain "
        "because they are additive evidence provenance and are not capture identity."
    ),
)
