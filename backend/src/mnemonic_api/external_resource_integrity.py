"""Cross-row invariants for external reservations in audited and restored projects."""

EXTERNAL_RESOURCE_FINDINGS = {
    "external_resource_over_capacity": """
        SELECT count(*) FROM (
            SELECT r.id FROM external_resources r
            JOIN external_resource_leases l ON l.resource_id=r.id
            WHERE l.ended_at IS NULL AND l.expires_at > statement_timestamp()
            GROUP BY r.id HAVING sum(l.quantity)>r.capacity
        ) over_capacity
    """,
    "external_activity_resource_mismatch": """
        SELECT count(*) FROM project_activity a
        LEFT JOIN external_resources r ON r.id=a.external_resource_id
        LEFT JOIN external_resource_leases l ON l.id=a.external_lease_id
        WHERE a.external_resource_id IS NOT NULL AND (
            a.project_id IS DISTINCT FROM r.project_id OR
            (a.external_lease_id IS NOT NULL AND l.resource_id IS DISTINCT FROM r.id))
    """,
}
