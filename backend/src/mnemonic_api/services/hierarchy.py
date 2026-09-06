"""Read-only hierarchy presentation queries."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import literal_column, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read, database_sqlstate
from mnemonic_api.errors import ApplicationError, not_found
from mnemonic_api.schemas import (
    ChildrenListQuery,
    HierarchySummary,
    LeasePublic,
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkItemRead,
)
from mnemonic_api.services.duplicates import (
    require_canonical_work_item,
    validate_project_duplicate_graph,
)
from mnemonic_api.services.readiness import (
    readiness,
    unresolved_blocker_count_clause,
    unresolved_gate_count_clause,
)

HIERARCHY_STATEMENT_TIMEOUT_MS = 5_000


def _hierarchy_match_sql(
    filters: WorkItemListQuery | ChildrenListQuery,
) -> tuple[str, dict[str, object]]:
    conditions = ["candidate.deleted_at IS NULL"]
    parameters: dict[str, object] = {}
    if filters.status == "active":
        conditions.append(
            "candidate.status = 'pending' AND EXISTS ("
            "SELECT 1 FROM work_leases AS filter_lease "
            "WHERE filter_lease.work_item_id = candidate.id "
            "AND filter_lease.expires_at > database_time.now"
            ")"
        )
    elif filters.status == "dropped":
        conditions.append(
            "candidate.status = 'pending' AND EXISTS ("
            "SELECT 1 FROM work_leases AS filter_lease "
            "WHERE filter_lease.work_item_id = candidate.id "
            "AND filter_lease.expires_at <= database_time.now"
            ")"
        )
    elif filters.status == "pending":
        conditions.append(
            "candidate.status = 'pending' AND NOT EXISTS ("
            "SELECT 1 FROM work_leases AS filter_lease "
            "WHERE filter_lease.work_item_id = candidate.id"
            ")"
        )
    elif filters.status != "all":
        conditions.append("candidate.status = :filter_status")
        parameters["filter_status"] = filters.status

    checkpoint_conditions: list[str] = []
    if filters.tag is not None:
        checkpoint_conditions.append(
            "EXISTS ("
            "SELECT 1 FROM unnest(filter_checkpoint.tags) AS candidate_tag(value) "
            "WHERE lower(candidate_tag.value) = :filter_tag"
            ")"
        )
        parameters["filter_tag"] = filters.tag
    if filters.source_client is not None:
        checkpoint_conditions.append("filter_checkpoint.source_client = :filter_source_client")
        parameters["filter_source_client"] = filters.source_client
    if filters.source_session_id is not None:
        checkpoint_conditions.append(
            "filter_checkpoint.source_session_id = :filter_source_session_id"
        )
        parameters["filter_source_session_id"] = filters.source_session_id
    if checkpoint_conditions:
        conditions.append(
            "EXISTS (SELECT 1 FROM checkpoints AS filter_checkpoint "
            "WHERE filter_checkpoint.work_item_id = candidate.id AND "
            + " AND ".join(checkpoint_conditions)
            + ")"
        )
    return " AND ".join(conditions), parameters


def hierarchy_page(
    database: Session,
    project_id: UUID,
    filters: WorkItemListQuery | ChildrenListQuery,
    *,
    parent_work_item_id: UUID | None = None,
) -> tuple[list[HierarchySummary], int]:
    """Return one coherent hierarchy page and full-branch presentation snapshot."""
    from mnemonic_api.services.work_items import require_project, require_work_item

    begin_coherent_read(database)
    require_project(database, project_id)
    if parent_work_item_id is not None:
        parent = require_work_item(database, project_id, parent_work_item_id)
        require_canonical_work_item(database, parent)
    validate_project_duplicate_graph(database, project_id)
    # The recursive page itself remains one data statement and one snapshot. These
    # transaction-local controls avoid expensive one-shot LLVM compilation and bound
    # damage from a corrupt or unexpectedly pathological graph. Transaction end
    # restores both defaults.
    database.execute(text("SET LOCAL jit = off"))
    database.execute(text(f"SET LOCAL statement_timeout = '{HIERARCHY_STATEMENT_TIMEOUT_MS}ms'"))
    if parent_work_item_id is None:
        candidate_sql = """
            SELECT root.id
            FROM work_items AS root
            WHERE root.project_id = :project_id
              AND root.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM work_duplicate_merges AS duplicate_merge
                  WHERE duplicate_merge.project_id = :project_id
                    AND duplicate_merge.source_work_item_id = root.id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM work_relationships AS parent_edge
                  JOIN work_items AS local_parent
                    ON local_parent.id = parent_edge.source_work_item_id
                   AND local_parent.project_id = :project_id
                   AND local_parent.deleted_at IS NULL
                  WHERE parent_edge.relationship_type = 'parent-child'
                    AND parent_edge.target_work_item_id = root.id
              )
        """
    else:
        candidate_sql = """
            SELECT child.id
            FROM work_relationships AS child_edge
            JOIN work_items AS child
              ON child.id = child_edge.target_work_item_id
             AND child.project_id = :project_id
             AND child.deleted_at IS NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM work_duplicate_merges AS duplicate_merge
                 WHERE duplicate_merge.project_id = :project_id
                   AND duplicate_merge.source_work_item_id = child.id
             )
            WHERE child_edge.relationship_type = 'parent-child'
              AND child_edge.source_work_item_id = :parent_work_item_id
        """

    root_ordering = {
        "updated": "root.updated_at DESC, root.id DESC",
        "created": "root.created_at DESC, root.id DESC",
        "priority": "root.priority DESC, root.updated_at DESC, root.id DESC",
    }[filters.sort]
    page_ordering = {
        "updated": "page_rows.updated_at DESC, page_rows.id DESC",
        "created": "page_rows.created_at DESC, page_rows.id DESC",
        "priority": ("page_rows.priority DESC, page_rows.updated_at DESC, page_rows.id DESC"),
    }[filters.sort]
    match_sql, match_parameters = _hierarchy_match_sql(filters)
    dialect = database.get_bind().dialect

    def sql(expression) -> str:
        return str(
            expression.compile(
                dialect=dialect,
                compile_kwargs={"literal_binds": True},
            )
        )

    member_blocker_count_sql = sql(
        unresolved_blocker_count_clause(
            literal_column("member.id"),
            literal_column("member.project_id"),
        )
    )
    member_gate_count_sql = sql(
        unresolved_gate_count_clause(literal_column("member.id"))
    )
    root_blocker_count_sql = sql(
        unresolved_blocker_count_clause(
            literal_column("root.id"),
            literal_column("root.project_id"),
        )
    )
    root_gate_count_sql = sql(
        unresolved_gate_count_clause(literal_column("root.id"))
    )
    try:
        row = (
            database.execute(
                text(
                    f"""
            WITH RECURSIVE
            database_time AS MATERIALIZED (
                SELECT transaction_timestamp() AS now
            ),
            scope AS MATERIALIZED (
                SELECT
                    EXISTS (
                        SELECT 1 FROM projects WHERE id = :project_id
                    ) AS project_exists,
                    CASE
                        WHEN CAST(:parent_work_item_id AS uuid) IS NULL THEN true
                        ELSE EXISTS (
                            SELECT 1
                            FROM work_items
                            WHERE project_id = :project_id
                              AND id = CAST(:parent_work_item_id AS uuid)
                              AND deleted_at IS NULL
                        )
                    END AS parent_exists
            ),
            candidate_branches AS MATERIALIZED (
                {candidate_sql}
            ),
            subtree(branch_id, member_id, visited_path) AS (
                SELECT
                    candidate.id,
                    candidate.id,
                    ARRAY[candidate.id]::uuid[]
                FROM candidate_branches AS candidate
                UNION ALL
                SELECT
                    subtree.branch_id,
                    child_edge.target_work_item_id,
                    subtree.visited_path || child_edge.target_work_item_id
                FROM subtree
                JOIN work_relationships AS child_edge
                  ON child_edge.relationship_type = 'parent-child'
                 AND child_edge.source_work_item_id = subtree.member_id
                JOIN work_items AS visible_child
                  ON visible_child.project_id = :project_id
                 AND visible_child.id = child_edge.target_work_item_id
                 AND visible_child.deleted_at IS NULL
                 AND NOT EXISTS (
                     SELECT 1
                     FROM work_duplicate_merges AS duplicate_merge
                     WHERE duplicate_merge.project_id = :project_id
                       AND duplicate_merge.source_work_item_id = visible_child.id
                 )
                WHERE NOT child_edge.target_work_item_id = ANY(subtree.visited_path)
            ),
            -- Qualification and paging deliberately precede member facts: branch
            -- aggregates cannot affect qualification, ordering, or the full total,
            -- so expensive correlated facts run only for branches on this page.
            matches AS MATERIALIZED (
                SELECT candidate.id
                FROM work_items AS candidate
                CROSS JOIN database_time
                WHERE candidate.project_id = :project_id
                  AND candidate.id IN (SELECT member_id FROM subtree)
                  AND {match_sql}
            ),
            qualifying AS MATERIALIZED (
                SELECT
                    subtree.branch_id,
                    bool_or(
                        subtree.member_id = subtree.branch_id
                        AND matches.id IS NOT NULL
                    ) AS self_matches_filter,
                    bool_or(
                        subtree.member_id <> subtree.branch_id
                        AND matches.id IS NOT NULL
                    ) AS has_matching_descendants
                FROM subtree
                LEFT JOIN matches ON matches.id = subtree.member_id
                GROUP BY subtree.branch_id
                HAVING bool_or(matches.id IS NOT NULL)
            ),
            paged AS MATERIALIZED (
                SELECT qualifying.*
                FROM qualifying
                JOIN work_items AS root ON root.id = qualifying.branch_id
                ORDER BY {root_ordering}
                LIMIT :limit OFFSET :offset
            ),
            member_facts AS MATERIALIZED (
                SELECT
                    subtree.branch_id,
                    subtree.member_id,
                    cardinality(subtree.visited_path) - 1 AS depth,
                    member.status,
                    (
                        member.status = 'pending'
                        AND ({member_blocker_count_sql}) > 0
                    ) AS is_blocked,
                    (
                        member.status = 'pending'
                        AND EXISTS (
                            SELECT 1
                            FROM work_leases AS active_member_lease
                            WHERE active_member_lease.work_item_id = member.id
                              AND active_member_lease.expires_at > database_time.now
                        )
                    ) AS is_active,
                    member.status = 'done' AS is_completed,
                    EXISTS (
                        SELECT 1
                        FROM work_relationships AS discovery_edge
                        WHERE discovery_edge.relationship_type = 'discovered-from'
                          AND discovery_edge.source_work_item_id = member.id
                    ) AS is_discovered,
                    ({member_gate_count_sql}) AS unresolved_gate_count,
                    (
                        SELECT active_member_lease.expires_at
                        FROM work_leases AS active_member_lease
                        WHERE active_member_lease.work_item_id = member.id
                          AND active_member_lease.expires_at > database_time.now
                        LIMIT 1
                    ) AS active_lease_expires_at
                FROM paged
                JOIN subtree ON subtree.branch_id = paged.branch_id
                JOIN work_items AS member
                  ON member.project_id = :project_id
                 AND member.id = subtree.member_id
                 AND member.deleted_at IS NULL
                CROSS JOIN database_time
            ),
            duplicate_members(
                branch_id,
                canonical_member_id,
                member_id,
                visited_path
            ) AS (
                SELECT
                    member_facts.branch_id,
                    member_facts.member_id,
                    member_facts.member_id,
                    ARRAY[member_facts.member_id]::uuid[]
                FROM member_facts
                UNION ALL
                SELECT
                    duplicate_members.branch_id,
                    duplicate_members.canonical_member_id,
                    duplicate_merge.source_work_item_id,
                    duplicate_members.visited_path || duplicate_merge.source_work_item_id
                FROM duplicate_members
                JOIN work_duplicate_merges AS duplicate_merge
                  ON duplicate_merge.project_id = :project_id
                 AND duplicate_merge.destination_work_item_id = duplicate_members.member_id
                JOIN work_items AS duplicate_work
                  ON duplicate_work.project_id = :project_id
                 AND duplicate_work.id = duplicate_merge.source_work_item_id
                 AND duplicate_work.deleted_at IS NULL
                WHERE cardinality(duplicate_members.visited_path) <= 50
                  AND NOT duplicate_merge.source_work_item_id = ANY(
                      duplicate_members.visited_path
                  )
            ),
            duplicate_aggregates AS MATERIALIZED (
                SELECT
                    duplicate_members.branch_id,
                    count(*) FILTER (
                        WHERE duplicate_members.member_id
                            <> duplicate_members.canonical_member_id
                    ) AS branch_merged_duplicate_count
                FROM duplicate_members
                GROUP BY duplicate_members.branch_id
            ),
            branch_aggregates AS MATERIALIZED (
                SELECT
                    facts.branch_id,
                    count(*) FILTER (WHERE facts.depth = 1) AS direct_child_count,
                    count(*) FILTER (WHERE facts.depth > 0) AS descendant_count,
                    count(*) FILTER (
                        WHERE facts.depth > 0 AND facts.is_blocked
                    ) AS blocked_descendant_count,
                    count(*) FILTER (
                        WHERE facts.depth > 0 AND facts.is_active
                    ) AS active_descendant_count,
                    count(*) FILTER (
                        WHERE facts.depth > 0 AND facts.is_completed
                    ) AS completed_descendant_count,
                    count(*) FILTER (
                        WHERE facts.depth > 0 AND facts.is_discovered
                    ) AS discovered_descendant_count,
                    sum(facts.unresolved_gate_count)
                        AS branch_unresolved_human_gate_count,
                    bool_or(
                        facts.member_id = facts.branch_id
                        AND facts.is_discovered
                    ) AS is_discovered_work,
                    min(facts.active_lease_expires_at) FILTER (
                        WHERE facts.depth > 0 AND facts.is_active
                    ) AS next_active_descendant_lease_expires_at
                FROM member_facts AS facts
                GROUP BY facts.branch_id
            ),
            page_rows AS MATERIALIZED (
                SELECT
                    root.*,
                    paged.self_matches_filter,
                    paged.has_matching_descendants,
                    aggregate.direct_child_count,
                    aggregate.descendant_count,
                    aggregate.blocked_descendant_count,
                    aggregate.active_descendant_count,
                    aggregate.completed_descendant_count,
                    aggregate.discovered_descendant_count,
                    aggregate.branch_unresolved_human_gate_count,
                    duplicate_aggregate.branch_merged_duplicate_count,
                    aggregate.is_discovered_work,
                    EXISTS (
                        SELECT 1
                        FROM work_relationships AS parent_edge
                        JOIN work_items AS local_parent
                          ON local_parent.id = parent_edge.source_work_item_id
                         AND local_parent.project_id = :project_id
                         AND local_parent.deleted_at IS NULL
                        JOIN work_relationships AS discovery_edge
                          ON discovery_edge.relationship_type = 'discovered-from'
                         AND discovery_edge.source_work_item_id =
                            parent_edge.target_work_item_id
                         AND discovery_edge.target_work_item_id =
                            parent_edge.source_work_item_id
                        WHERE parent_edge.relationship_type = 'parent-child'
                          AND parent_edge.target_work_item_id = paged.branch_id
                    ) AS discovered_from_parent,
                    aggregate.next_active_descendant_lease_expires_at,
                    current_checkpoint.id AS current_checkpoint_id,
                    current_checkpoint.kind AS current_checkpoint_kind,
                    current_checkpoint.source_client AS current_source_client,
                    current_checkpoint.source_session_id AS current_source_session_id,
                    current_checkpoint.source_model AS current_source_model,
                    current_checkpoint.repository_branch AS current_repository_branch,
                    current_checkpoint.verified_against AS current_verified_against,
                    current_checkpoint.tags AS current_tags,
                    current_checkpoint.migration_origin AS current_migration_origin,
                    current_checkpoint.legacy_record_id AS current_legacy_record_id,
                    current_checkpoint.created_at AS current_checkpoint_created_at,
                    (
                        SELECT count(*)
                        FROM checkpoints AS checkpoint_count
                        WHERE checkpoint_count.work_item_id = root.id
                    ) AS checkpoint_count,
                    ({root_blocker_count_sql}) AS unresolved_blocker_count,
                    ({root_gate_count_sql}) AS unresolved_gate_count,
                    EXISTS (
                        SELECT 1
                        FROM work_leases AS dropped_lease
                        WHERE dropped_lease.work_item_id = root.id
                          AND dropped_lease.expires_at <= database_time.now
                    ) AS has_dropped_lease,
                    active_lease.holder_client AS active_holder_client,
                    active_lease.holder_session_id AS active_holder_session_id,
                    active_lease.acquired_at AS active_acquired_at,
                    active_lease.renewed_at AS active_renewed_at,
                    active_lease.expires_at AS active_expires_at
                FROM paged
                JOIN branch_aggregates AS aggregate
                  ON aggregate.branch_id = paged.branch_id
                JOIN duplicate_aggregates AS duplicate_aggregate
                  ON duplicate_aggregate.branch_id = paged.branch_id
                JOIN work_items AS root ON root.id = paged.branch_id
                CROSS JOIN database_time
                JOIN LATERAL (
                    SELECT checkpoint.*
                    FROM checkpoints AS checkpoint
                    WHERE checkpoint.work_item_id = root.id
                      AND checkpoint.kind = 'context'
                    ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
                    LIMIT 1
                ) AS current_checkpoint ON true
                LEFT JOIN work_leases AS active_lease
                  ON active_lease.work_item_id = root.id
                 AND active_lease.expires_at > database_time.now
            )
            SELECT
                (SELECT project_exists FROM scope) AS project_exists,
                (SELECT parent_exists FROM scope) AS parent_exists,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'summary', jsonb_build_object(
                                'work_item', jsonb_build_object(
                                    'id', page_rows.id,
                                    'project_id', page_rows.project_id,
                                    'title', page_rows.title,
                                    'summary', page_rows.summary,
                                    'external_references', page_rows.external_references,
                                    'status', page_rows.status,
                                    'priority', page_rows.priority,
                                    'initial_checkpoint_id',
                                        page_rows.initial_checkpoint_id,
                                    'version', page_rows.version,
                                    'created_at', page_rows.created_at,
                                    'updated_at', page_rows.updated_at
                                ),
                                'checkpoint_count', page_rows.checkpoint_count,
                                'ancestor_path', '[]'::jsonb,
                                'ancestor_path_truncated', false,
                                'current_context', jsonb_build_object(
                                    'id', page_rows.current_checkpoint_id,
                                    'work_item_id', page_rows.id,
                                    'kind', page_rows.current_checkpoint_kind,
                                    'source_client', page_rows.current_source_client,
                                    'source_session_id',
                                        page_rows.current_source_session_id,
                                    'source_model', page_rows.current_source_model,
                                    'repository_branch',
                                        page_rows.current_repository_branch,
                                    'verified_against',
                                        page_rows.current_verified_against,
                                    'tags', page_rows.current_tags,
                                    'migration_origin',
                                        page_rows.current_migration_origin,
                                    'legacy_record_id',
                                        page_rows.current_legacy_record_id,
                                    'created_at',
                                        page_rows.current_checkpoint_created_at
                                ),
                                'readiness', jsonb_build_object(
                                    'has_dropped_lease',
                                        page_rows.has_dropped_lease,
                                    'active_lease', CASE
                                        WHEN page_rows.active_expires_at IS NULL THEN NULL
                                        ELSE jsonb_build_object(
                                            'holder_client',
                                                page_rows.active_holder_client,
                                            'holder_session_id',
                                                page_rows.active_holder_session_id,
                                            'acquired_at',
                                                page_rows.active_acquired_at,
                                            'renewed_at',
                                                page_rows.active_renewed_at,
                                            'expires_at',
                                                page_rows.active_expires_at
                                        )
                                    END,
                                    'unresolved_blocker_count',
                                        page_rows.unresolved_blocker_count,
                                    'unresolved_gate_count',
                                        page_rows.unresolved_gate_count
                                )
                            ),
                            'self_matches_filter',
                                page_rows.self_matches_filter,
                            'has_matching_descendants',
                                page_rows.has_matching_descendants,
                            'presentation', jsonb_build_object(
                                'direct_child_count',
                                    page_rows.direct_child_count,
                                'descendant_count',
                                    page_rows.descendant_count,
                                'blocked_descendant_count',
                                    page_rows.blocked_descendant_count,
                                'active_descendant_count',
                                    page_rows.active_descendant_count,
                                'completed_descendant_count',
                                    page_rows.completed_descendant_count,
                                'discovered_descendant_count',
                                    page_rows.discovered_descendant_count,
                                'branch_unresolved_human_gate_count',
                                    page_rows.branch_unresolved_human_gate_count,
                                'branch_merged_duplicate_count',
                                    page_rows.branch_merged_duplicate_count,
                                'is_discovered_work',
                                    page_rows.is_discovered_work,
                                'discovered_from_parent',
                                    page_rows.discovered_from_parent,
                                'next_active_descendant_lease_expires_at',
                                    page_rows.next_active_descendant_lease_expires_at
                            )
                        )
                        ORDER BY {page_ordering}
                    ) FILTER (WHERE page_rows.id IS NOT NULL),
                    '[]'::jsonb
                ) AS items,
                (SELECT count(*) FROM qualifying) AS total
            FROM page_rows
            """
                ),
                {
                    "project_id": project_id,
                    "parent_work_item_id": parent_work_item_id,
                    "limit": filters.limit,
                    "offset": filters.offset,
                    **match_parameters,
                },
            )
            .mappings()
            .one()
        )
    except DBAPIError as exc:
        if database_sqlstate(exc) != "57014":
            raise
        database.rollback()
        raise ApplicationError(
            503,
            "hierarchy_timeout",
            "Hierarchy traversal exceeded its safety limit; narrow the view or "
            "investigate the graph.",
        ) from None
    if not row["project_exists"]:
        raise not_found("project_not_found", "Project not found.")
    if not row["parent_exists"]:
        raise not_found("work_item_not_found", "Work item not found in this project.")
    items: list[HierarchySummary] = []
    for item in row["items"]:
        summary = item["summary"]
        readiness_inputs = summary["readiness"]
        active_lease = readiness_inputs["active_lease"]
        summary["readiness"] = readiness(
            WorkItemRead.model_validate(summary["work_item"]),
            LeasePublic.model_validate(active_lease) if active_lease is not None else None,
            int(readiness_inputs["unresolved_blocker_count"]),
            bool(readiness_inputs["has_dropped_lease"]),
            int(readiness_inputs["unresolved_gate_count"]),
            canonical_work_item_id=UUID(str(summary["work_item"]["id"])),
        )
        items.append(HierarchySummary.model_validate(item))
    return items, int(row["total"])


def ancestor_paths(
    database: Session, project_id: UUID, work_item_ids: Sequence[UUID]
) -> tuple[dict[UUID, list[WorkIdentityPointer]], set[UUID]]:
    if not work_item_ids:
        return {}, set()
    rows = database.execute(
        text(
            """
            WITH RECURSIVE ancestors(work_item_id, ancestor_id, depth) AS (
                SELECT edge.target_work_item_id, edge.source_work_item_id, 1
                FROM work_relationships AS edge
                JOIN work_items AS local_ancestor
                  ON local_ancestor.id = edge.source_work_item_id
                 AND local_ancestor.project_id = :project_id
                 AND local_ancestor.deleted_at IS NULL
                WHERE edge.relationship_type = 'parent-child'
                  AND edge.target_work_item_id = ANY(CAST(:work_item_ids AS uuid[]))
                UNION ALL
                SELECT ancestors.work_item_id, edge.source_work_item_id, ancestors.depth + 1
                FROM ancestors
                JOIN work_relationships AS edge
                  ON edge.relationship_type = 'parent-child'
                 AND edge.target_work_item_id = ancestors.ancestor_id
                JOIN work_items AS local_ancestor
                  ON local_ancestor.id = edge.source_work_item_id
                 AND local_ancestor.project_id = :project_id
                 AND local_ancestor.deleted_at IS NULL
                WHERE ancestors.depth < 51
            )
            SELECT
                ancestors.work_item_id,
                ancestors.depth,
                ancestor.id,
                ancestor.title,
                ancestor.status
            FROM ancestors
            JOIN work_items AS ancestor
              ON ancestor.id = ancestors.ancestor_id
             AND ancestor.deleted_at IS NULL
            ORDER BY ancestors.work_item_id, ancestors.depth DESC
            """
        ),
        {"project_id": project_id, "work_item_ids": list(work_item_ids)},
    ).mappings()
    paths: dict[UUID, list[WorkIdentityPointer]] = {
        work_item_id: [] for work_item_id in work_item_ids
    }
    truncated: set[UUID] = set()
    for row in rows:
        work_item_id = UUID(str(row["work_item_id"]))
        if int(row["depth"]) > 50:
            truncated.add(work_item_id)
            continue
        paths[work_item_id].append(
            WorkIdentityPointer(id=row["id"], title=row["title"], status=row["status"])
        )
    return paths, truncated
