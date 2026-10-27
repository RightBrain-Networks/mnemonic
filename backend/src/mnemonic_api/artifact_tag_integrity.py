"""Read-only checks for retained artifact labels and the current project vocabulary."""

ARTIFACT_TAG_FINDINGS = {
    "artifact_current_tags_mismatch": """
        SELECT count(*) FROM artifacts artifact
        JOIN artifact_revisions revision
          ON revision.artifact_id=artifact.id AND revision.revision=artifact.revision
        WHERE artifact.tags IS DISTINCT FROM revision.tags
    """,
    "artifact_unknown_current_tags": """
        SELECT count(*) FROM artifacts artifact
        WHERE artifact.deleted_at IS NULL AND artifact.revision > 0 AND EXISTS (
            SELECT 1 FROM unnest(artifact.tags) assigned(name)
            WHERE NOT EXISTS (SELECT 1 FROM project_tags catalog
                WHERE catalog.project_id=artifact.project_id AND catalog.name=assigned.name)
        )
    """,
    "artifact_invalid_tag_history": """
        SELECT count(*) FROM (
            SELECT tags FROM artifacts UNION ALL SELECT tags FROM artifact_revisions
        ) assignments WHERE tags IS NOT NULL
          AND mnemonic_assigned_tags_valid(to_jsonb(tags)) IS DISTINCT FROM true
    """,
}
