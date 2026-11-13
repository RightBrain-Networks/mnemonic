"""Read-only checks for the project reporting journal's durable sequence bounds."""

PROJECT_CHANGE_FINDINGS = {
    "project_change_head_missing": """
        SELECT count(*) FROM projects p
        LEFT JOIN project_change_heads h ON h.project_id=p.id
        WHERE h.project_id IS NULL
    """,
    "project_change_sequence_mismatch": """
        SELECT count(*) FROM project_change_heads h
        LEFT JOIN LATERAL (
            SELECT count(*) AS amount, coalesce(max(sequence),0) AS last,
                   coalesce(min(sequence),1) AS first
            FROM project_changes c WHERE c.project_id=h.project_id
        ) facts ON true
        WHERE h.last_sequence<>facts.last OR h.last_sequence<>facts.amount OR facts.first<>1
    """,
    "project_change_capture_time_invalid": """
        SELECT count(*) FROM project_changes c JOIN project_change_heads h
          ON h.project_id=c.project_id
        WHERE c.recorded_at<h.capture_started_at
    """,
}
