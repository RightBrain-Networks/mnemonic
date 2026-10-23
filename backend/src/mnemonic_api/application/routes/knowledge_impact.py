"""Bounded impact reads and receipt-protected reassessment dispositions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.impact_schemas import (
    ImpactAssessmentDetail,
    ImpactAssessmentQuery,
    ImpactAssessmentRead,
    ImpactDispositionCreate,
    ImpactDispositionRead,
    ImpactPageQuery,
    ImpactQuery,
    ImpactRead,
)
from mnemonic_api.knowledge_schemas import DecisionReference, KnowledgePage, KnowledgePagination
from mnemonic_api.services import decision_impact

router = APIRouter()
_PREFIX = "/projects/{project_id}/knowledge"


@router.get(_PREFIX + "/decisions/{decision_id}/impact", response_model=ImpactRead)
def get_decision_impact(
    project_id: UUID,
    decision_id: DecisionReference,
    database: Database,
    filters: Annotated[ImpactQuery, Query()],
) -> ImpactRead:
    begin_coherent_read(database)
    return decision_impact.read_impact(database, project_id, decision_id, filters)


@router.get(_PREFIX + "/assessments", response_model=KnowledgePage[ImpactAssessmentRead])
def list_decision_assessments(
    project_id: UUID, database: Database, filters: Annotated[ImpactAssessmentQuery, Query()]
) -> KnowledgePage[ImpactAssessmentRead]:
    begin_coherent_read(database)
    return decision_impact.list_assessments(database, project_id, filters)


@router.get(_PREFIX + "/assessments/{assessment_id}", response_model=ImpactAssessmentDetail)
def get_decision_assessment(
    project_id: UUID,
    assessment_id: UUID,
    database: Database,
    filters: Annotated[ImpactPageQuery, Query()],
) -> ImpactAssessmentDetail:
    begin_coherent_read(database)
    return decision_impact.assessment_detail(database, project_id, assessment_id, filters)


@router.get(
    _PREFIX + "/assessments/{assessment_id}/dispositions",
    response_model=KnowledgePage[ImpactDispositionRead],
)
def get_decision_assessment_history(
    project_id: UUID,
    assessment_id: UUID,
    database: Database,
    filters: Annotated[KnowledgePagination, Query()],
) -> KnowledgePage[ImpactDispositionRead]:
    begin_coherent_read(database)
    return decision_impact.disposition_history(database, project_id, assessment_id, filters)


@router.post(
    _PREFIX + "/assessments/{assessment_id}/dispositions",
    response_model=ImpactDispositionRead,
    status_code=201,
)
def assess_decision_impact(
    project_id: UUID,
    assessment_id: UUID,
    payload: ImpactDispositionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "assess_decision_impact",
        request=request,
        database=database,
        project_id=project_id,
        target={"assessment_id": assessment_id},
        payload=payload,
        execute=lambda body: decision_impact.append_disposition(
            database, project_id, assessment_id, body
        ),
    )
