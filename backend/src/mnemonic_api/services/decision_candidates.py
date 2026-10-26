"""Group captured code-impact candidates without coalescing any episode obligation."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.decision_candidate_schemas import (
    DecisionCandidateGroup,
    DecisionCandidateOccurrence,
    DecisionChangeCandidatesQuery,
    DecisionChangeCandidatesRead,
)
from mnemonic_api.services.decision_changes import require_change
from mnemonic_api.services.work_items import require_project

# Every invocation runs inside the route's coherent read transaction. These are
# immutable captured candidates; live decisions/links never reclassify old scope.
_OCCURRENCES = """
    WITH episodes AS (
        SELECT assessment.*,
            domain.prefix||'-'||lpad(document.ordinal::text,
                greatest(5,length(document.ordinal::text)),'0')||'-'||revision.revision::text
                AS after_reference,
            coalesce((SELECT max(disposition.version) FROM decision_impact_dispositions disposition
                      WHERE disposition.assessment_id=assessment.id),0) AS assessment_version
        FROM decision_change_assessments binding
        JOIN decision_impact_assessments assessment ON assessment.id=binding.assessment_id
        JOIN decision_revisions revision ON revision.id=assessment.replacement_revision_id
        JOIN decision_documents document ON document.id=revision.document_id
        JOIN knowledge_domains domain ON domain.id=document.domain_id
        WHERE binding.change_id=:change_id AND assessment.project_id=:project_id
    ), latest AS (
        SELECT DISTINCT ON (disposition.assessment_id, disposition.candidate_reference)
            disposition.*
        FROM decision_impact_dispositions disposition
        JOIN episodes ON episodes.id=disposition.assessment_id
        ORDER BY disposition.assessment_id, disposition.candidate_reference,
            disposition.version DESC
    ), occurrences AS (
        SELECT episodes.id AS assessment_id, episodes.created_at AS assessment_created_at,
            episodes.assessment_version, episodes.trigger, episodes.after_reference,
            candidate->'path'->>0 AS before_reference,
            candidate->>'decision_id' AS decision_id,
            candidate->>'reference' AS candidate_reference,
            candidate->>'relationship' AS relationship,
            candidate->>'related_direction' AS related_direction,
            CASE WHEN latest.id IS NOT NULL THEN jsonb_build_object(
                'id',latest.id,'version',latest.version,'disposition',latest.disposition,
                'remediation_work_item_id',latest.remediation_work_item_id,
                'finding_id',latest.finding_id,'remediation_target',latest.remediation_target)
            WHEN source.id IS NOT NULL THEN jsonb_build_object(
                'id',source.id,'version',source.version,'disposition',source.disposition,
                'remediation_work_item_id',source.remediation_work_item_id,
                'finding_id',source.finding_id,'remediation_target',source.remediation_target,
                'reused_from',jsonb_build_object('assessment_id',source.assessment_id,
                    'disposition_id',source.id,'disposition_version',source.version))
            ELSE NULL END AS latest_disposition
        FROM episodes CROSS JOIN LATERAL jsonb_array_elements(episodes.candidates) candidate
        LEFT JOIN latest ON latest.assessment_id=episodes.id
            AND latest.candidate_reference=candidate->>'reference'
        LEFT JOIN decision_candidate_reuses reuse ON reuse.assessment_id=episodes.id
            AND reuse.candidate_reference=candidate->>'reference'
        LEFT JOIN decision_impact_dispositions source ON source.id=reuse.source_disposition_id
        WHERE candidate->>'relationship' IN ('origin','depends-on')
            OR (candidate->>'relationship'='related-only'
                AND candidate->>'related_direction'='incoming')
    )
"""
_COUNTS = """
    count(*) AS occurrence_count,
    count(DISTINCT candidate_reference) AS revision_count,
    count(*) FILTER (WHERE latest_disposition IS NULL) AS unassessed_count,
    count(*) FILTER (WHERE latest_disposition->>'disposition'='change-required')
        AS change_required_count
"""


def _groups(database: Session, parameters: dict) -> list[dict]:
    return [dict(row) for row in database.execute(text(_OCCURRENCES + f"""
        SELECT decision_id, {_COUNTS} FROM occurrences
        WHERE CAST(:decision_id AS text) IS NULL OR decision_id=:decision_id
        GROUP BY decision_id ORDER BY decision_id LIMIT :limit OFFSET :offset
    """), parameters).mappings()]


def _pages(database: Session, parameters: dict, groups: list[dict]) -> dict[
    str, list[DecisionCandidateOccurrence]
]:
    result = defaultdict(list)
    if not groups:
        return result
    rows = database.execute(text(_OCCURRENCES + """
        , ranked AS (
            SELECT *, row_number() OVER (PARTITION BY decision_id
                ORDER BY assessment_created_at, assessment_id, candidate_reference) AS position
            FROM occurrences WHERE decision_id=ANY(CAST(:selected_decisions AS text[]))
        ) SELECT * FROM ranked WHERE position>:occurrence_offset
            AND position<=:occurrence_offset+:occurrence_limit
        ORDER BY decision_id, position
    """), {**parameters, "selected_decisions": [row["decision_id"] for row in groups]}).mappings()
    for row in rows:
        values = {key: row[key] for key in DecisionCandidateOccurrence.model_fields}
        result[row["decision_id"]].append(DecisionCandidateOccurrence.model_validate(values))
    return result


def _group(
    row: dict, items: list[DecisionCandidateOccurrence], offset: int,
) -> DecisionCandidateGroup:
    references = sorted({item.candidate_reference for item in items})
    end = offset + len(items)
    return DecisionCandidateGroup(
        **row, captured_references=references, references_complete=len(references) == row[
            "revision_count"
        ], occurrences=items, next_occurrence_offset=end if end < row["occurrence_count"] else None,
    )


def read_change_candidates(
    database: Session, project_id: UUID, change_id: UUID, filters: DecisionChangeCandidatesQuery,
) -> DecisionChangeCandidatesRead:
    require_project(database, project_id)
    change = require_change(database, project_id, change_id)
    parameters = {"project_id": project_id, "change_id": change_id, **filters.model_dump()}
    totals = dict(database.execute(text(_OCCURRENCES + f"""
        SELECT count(DISTINCT decision_id) AS decision_count, {_COUNTS},
            count(DISTINCT decision_id) FILTER (
                WHERE CAST(:decision_id AS text) IS NULL OR decision_id=:decision_id) AS total,
            transaction_timestamp() AS observed_at
        FROM occurrences
    """), parameters).mappings().one())
    groups = _groups(database, parameters)
    pages = _pages(database, parameters, groups)
    end = filters.offset + len(groups)
    return DecisionChangeCandidatesRead(
        project_id=project_id, change_id=change_id, scope_version=change.scope_version,
        acknowledged_scope_version=change.acknowledged_scope_version,
        assessment_count=change.scope_version, **totals, **filters.model_dump(),
        items=[_group(row, pages[row["decision_id"]], filters.occurrence_offset) for row in groups],
        next_offset=end if end < totals["total"] else None,
    )
