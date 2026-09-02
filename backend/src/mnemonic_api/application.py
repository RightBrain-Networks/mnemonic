"""Authenticated, project-scoped REST API for canonical work and checkpoints."""

import logging
import secrets
from collections.abc import Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory, get_session
from mnemonic_api.errors import ApplicationError, conflict, human_gates_not_enabled
from mnemonic_api.live_sync import LiveSyncHub, mutation_event
from mnemonic_api.models import Checkpoint, Project, ProjectSettings, WorkItem, WorkLease
from mnemonic_api.schemas import (
    AdjacentRelationshipRead,
    APIModel,
    CheckpointCreate,
    CheckpointListQuery,
    CheckpointRead,
    ChildrenListQuery,
    ClaimAndRecall,
    ClaimReceipt,
    HierarchySummary,
    HumanAttentionListQuery,
    HumanAttentionPage,
    HumanGateListQuery,
    HumanGatePage,
    HumanGateRead,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    LeaseReleaseCreate,
    LeaseTokenCreate,
    Page,
    ProgressEventCreate,
    ProjectCreate,
    ProjectListQuery,
    ProjectPatch,
    ProjectRead,
    ProjectSettingsPatch,
    ProjectSettingsRead,
    ReadyWorkListQuery,
    ReadyWorkPage,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipListQuery,
    RelationshipRemovalCreate,
    RelationshipRemovalResult,
    ReleaseResult,
    WorkClaimCreate,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkContext,
    WorkContextQuery,
    WorkCreation,
    WorkDeferralCreate,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkEventListQuery,
    WorkEventPage,
    WorkEventRead,
    WorkItemCreate,
    WorkItemListQuery,
    WorkItemPatch,
    WorkItemRead,
    WorkSummary,
    WorkSummaryMinimal,
)
from mnemonic_api.semantic import Embedder, FastembedEmbedder, hybrid_rank
from mnemonic_api.services.client_operations import (
    CompletedOperation,
    OperationKind,
    ReplayedOperation,
    ReservationOutcome,
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)
from mnemonic_api.services.gates import (
    list_human_attention,
    list_work_gates,
    reject_gate_secret_echo,
    reject_retained_gate_control_echo,
    request_human_gate,
    resolve_human_gate,
)
from mnemonic_api.services.leases import (
    claim_lease_record,
    release_lease_record,
    renew_lease_record,
)
from mnemonic_api.services.readiness import ready_work_page
from mnemonic_api.services.relationships import (
    add_relationship_record,
    ancestor_paths,
    hierarchy_page,
    list_adjacent_relationships,
    relationship_edge,
    remove_relationship_record,
    require_relationship,
)
from mnemonic_api.services.work_context import (
    assemble_work_context,
    checkpoint_read,
    minimal_work_summaries,
    work_summaries,
)
from mnemonic_api.services.work_events import append_progress_event, list_work_events
from mnemonic_api.services.work_items import (
    append_checkpoint_record,
    complete_work_record,
    create_work_records,
    defer_work_record,
    delete_work_record,
    require_project,
    require_work_item,
    update_work_record,
)

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
Database = Annotated[Session, Depends(get_session)]

# Covered mutation routes set this only after their transaction has committed.
# Its absence deliberately preserves the successful-method/path fallback for
# project administration, claims, renewals, and future unenrolled writes.
_MUTATION_APPLIED_STATE = "mnemonic_mutation_applied"
_CLIENT_OPERATION_KIND_STATE = "mnemonic_client_operation_kind"
_CLIENT_OPERATION_OUTCOME_STATE = "mnemonic_client_operation_outcome"
_CLIENT_OPERATION_HEADER_NAMES = frozenset(
    {
        "client_operation_id",
        "client-operation-id",
        "idempotency-key",
        "x-idempotency-key",
        "x-client-operation-id",
    }
)

_PUBLIC_VALIDATION_LOCATION_REPLACEMENT = "field"
_PUBLIC_VALIDATION_LOCATION_SEGMENTS = frozenset(
    """
    body query path header cookie project_id work_item_id relationship_id
    name description slug q semantic status tag source_client source_session_id
    view sort limit offset min_priority parent_work_item_id direction type order
    event_type recent_limit recent_event_limit title summary priority expected_version
    initial_checkpoint initial_relationships checkpoint kind prompt source_model
    source_session_url repository_branch verified_against tags source_metadata
    migration_origin legacy_record_id relationship_type source_work_item_id
    target_work_item_id other_work_item_id context_checkpoint_id created_by_client
    created_by_session_id created_by_model holder_client holder_session_id
    claim_request_id client_operation_id lease_token actor actor_client actor_session_id
    actor_model metadata gate_id gate_type question resolution
    requested_by_client requested_by_session_id requested_by_model
    resolved_by_client resolved_by_session_id resolved_by_model
    acknowledge_context_change reviewed_context_revision current_context_revision
    requested_work_version requested_context_checkpoint_id
    requested_relationship_event_count resolved_context_revision
    resolved_work_version resolved_context_checkpoint_id
    resolved_relationship_event_count relationship_event_count work_version cursor
    focus_gate_id
    work_item_id
    recall_pointer_template
    """.split()
)

_PUBLIC_VALIDATION_ERROR_MESSAGES = {
    "assertion_error": "Value is invalid.",
    "bool_parsing": "Input should be a valid boolean.",
    "bool_type": "Input should be a valid boolean.",
    "datetime_parsing": "Input should be a valid datetime.",
    "datetime_type": "Input should be a valid datetime.",
    "dict_type": "Input should be an object.",
    "extra_forbidden": "Extra inputs are not permitted.",
    "finite_number": "Input should be a finite number.",
    "float_parsing": "Input should be a valid number.",
    "float_type": "Input should be a valid number.",
    "greater_than_equal": "Input is below the allowed minimum.",
    "int_parsing": "Input should be a valid integer.",
    "int_type": "Input should be a valid integer.",
    "json_invalid": "Request body contains invalid JSON.",
    "less_than_equal": "Input exceeds the allowed maximum.",
    "list_type": "Input should be a list.",
    "literal_error": "Input has an unsupported value.",
    "missing": "Field required.",
    "model_attributes_type": "Input should be an object.",
    "string_pattern_mismatch": "String format is invalid.",
    "string_too_long": "String is too long.",
    "string_too_short": "String is too short.",
    "string_type": "Input should be a valid string.",
    "too_long": "Collection contains too many items.",
    "too_short": "Collection contains too few items.",
    "uuid_parsing": "Input should be a valid UUID.",
    "uuid_type": "Input should be a valid UUID.",
    "value_error": "Value is invalid.",
}


def _public_validation_errors(
    errors: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    public_errors: list[dict[str, object]] = []
    for error in errors:
        raw_type = error.get("type")
        error_type = (
            raw_type
            if isinstance(raw_type, str) and raw_type in _PUBLIC_VALIDATION_ERROR_MESSAGES
            else "validation_error"
        )
        raw_location = error.get("loc")
        location_parts = (
            raw_location
            if isinstance(raw_location, (list, tuple))
            else ()
        )
        public_location: list[str | int] = []
        for part in location_parts:
            if isinstance(part, int) and not isinstance(part, bool):
                public_location.append(part)
            elif isinstance(part, str) and part in _PUBLIC_VALIDATION_LOCATION_SEGMENTS:
                public_location.append(part)
            else:
                public_location.append(_PUBLIC_VALIDATION_LOCATION_REPLACEMENT)
        public_errors.append(
            {
                "type": error_type,
                "loc": public_location,
                "msg": _PUBLIC_VALIDATION_ERROR_MESSAGES.get(
                    error_type,
                    "Request validation failed.",
                ),
            }
        )

    return public_errors


def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> None:
    expected = request.app.state.settings.api_key.get_secret_value().encode("utf-8")
    supplied = credentials.credentials.encode("utf-8") if credentials else b""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Valid bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _request_has_valid_bearer(request: Request) -> bool:
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied_text = authorization.partition(" ")
    supplied = (
        supplied_text.encode("utf-8")
        if separator and scheme.casefold() == "bearer" and supplied_text
        else b""
    )
    expected = request.app.state.settings.api_key.get_secret_value().encode("utf-8")
    return secrets.compare_digest(supplied, expected)


def _unauthenticated_response() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"detail": "Valid bearer authentication is required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _reserve_registered_operation(
    kind: OperationKind,
    project_id: UUID,
    target_envelope: Mapping[str, UUID],
    payload: APIModel,
    request: Request,
    database: Session,
) -> ReservationOutcome:
    reject_registered_mutation_query(request)
    setattr(request.state, _CLIENT_OPERATION_KIND_STATE, kind)
    try:
        prepared = prepare_client_operation(
            kind,
            project_id,
            target_envelope,
            payload,
            known_secret_values=(
                request.app.state.settings.api_key.get_secret_value(),
            ),
        )
        return reserve_client_operation(
            database,
            prepared,
            wait_seconds=request.app.state.settings.client_operation_wait_seconds,
        )
    except ApplicationError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        outcome = {
            "client_operation_conflict": "conflict",
            "client_operation_unavailable": "unavailable",
        }.get(code)
        if outcome is not None:
            setattr(request.state, _CLIENT_OPERATION_OUTCOME_STATE, outcome)
        raise


def _record_successful_operation(
    request: Request,
    operation: CompletedOperation | ReplayedOperation,
) -> None:
    setattr(request.state, _MUTATION_APPLIED_STATE, operation.mutation_applied)
    if not operation.mutation_applied:
        outcome = "no_op"
    elif isinstance(operation, ReplayedOperation):
        outcome = "replayed"
    else:
        outcome = "executed"
    setattr(request.state, _CLIENT_OPERATION_KIND_STATE, operation.spec.kind)
    setattr(request.state, _CLIENT_OPERATION_OUTCOME_STATE, outcome)


def _raise_query_rejection(message: str, *, field: str | None = None) -> None:
    location = ["query", field] if field is not None else ["query"]
    raise HTTPException(
        status_code=422,
        detail=[
            {
                "type": "extra_forbidden",
                "loc": location,
                "msg": message,
            }
        ],
    )


def reject_lease_token_query(request: Request) -> None:
    # Never inspect, echo, or log a query value. Production access logging is
    # disabled as a second boundary because URLs are not secret-safe.
    if "lease_token" in request.query_params:
        _raise_query_rejection(
            "Lease tokens are accepted only in JSON request bodies.",
            field="lease_token",
        )


def reject_client_operation_transport(request: Request) -> None:
    """Reject operation IDs anywhere except a supported JSON request body."""
    if any(
        key.strip().casefold() in _CLIENT_OPERATION_HEADER_NAMES
        for key in request.query_params
    ):
        _raise_query_rejection(
            "Client operation IDs are accepted only in supported JSON request bodies.",
            field="client_operation_id",
        )
    if any(
        key.casefold() in _CLIENT_OPERATION_HEADER_NAMES
        for key in request.headers
    ):
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "extra_forbidden",
                    "loc": ["header", "client_operation_id"],
                    "msg": (
                        "Client operation IDs are accepted only in supported JSON "
                        "request bodies."
                    ),
                }
            ],
        )
    if any(
        name.strip().casefold() in _CLIENT_OPERATION_HEADER_NAMES
        for name in request.cookies
    ):
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "extra_forbidden",
                    "loc": ["cookie", "client_operation_id"],
                    "msg": (
                        "Client operation IDs are accepted only in supported JSON "
                        "request bodies."
                    ),
                }
            ],
        )


def reject_registered_mutation_query(request: Request) -> None:
    """Keep the ten receipt-protected REST mutation routes query-free."""
    if request.query_params:
        _raise_query_rejection(
            "Query parameters are not accepted for registered mutations."
        )


def reject_lease_operation_query(request: Request) -> None:
    if request.query_params:
        _raise_query_rejection("Query parameters are not accepted for lease operations.")


# The pre-routing HTTP middleware authenticates before FastAPI reads a body.
# Retain this dependency as route-local defense in depth and before the
# capability/query validators that follow it.
router = APIRouter(
    prefix="/api/v1",
    dependencies=[
        Depends(authenticate),
        Depends(reject_lease_token_query),
        Depends(reject_client_operation_transport),
    ],
)
sync_router = APIRouter(prefix="/api/v1")


def _matching_checkpoint_exists(work_item_id, *conditions):
    return (
        select(Checkpoint.id).where(Checkpoint.work_item_id == work_item_id, *conditions).exists()
    )


def _search_work_rows(
    project_id: UUID,
    filters: WorkItemListQuery,
    request: Request,
    database: Session,
) -> tuple[list[WorkItem], int]:
    require_project(database, project_id)
    conditions = [WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None)]
    if filters.status in {"active", "dropped"}:
        lease_expiry_condition = (
            WorkLease.expires_at > func.clock_timestamp()
            if filters.status == "active"
            else WorkLease.expires_at <= func.clock_timestamp()
        )
        retained_lease_exists = (
            select(WorkLease.work_item_id)
            .where(
                WorkLease.work_item_id == WorkItem.id,
                lease_expiry_condition,
            )
            .correlate(WorkItem)
            .exists()
        )
        conditions.extend([WorkItem.status == "pending", retained_lease_exists])
    elif filters.status == "pending":
        retained_lease_exists = (
            select(WorkLease.work_item_id)
            .where(WorkLease.work_item_id == WorkItem.id)
            .correlate(WorkItem)
            .exists()
        )
        conditions.extend([WorkItem.status == "pending", ~retained_lease_exists])
    elif filters.status != "all":
        conditions.append(WorkItem.status == filters.status)

    checkpoint_filters = []
    if filters.tag is not None:
        checkpoint_tag = func.unnest(Checkpoint.tags).column_valued("checkpoint_tag")
        checkpoint_filters.append(
            or_(
                # Keep the indexed normalized-data fast path while allowing
                # exact migrations that preserved historical tag case.
                Checkpoint.tags.contains([filters.tag]),
                select(1).where(func.lower(checkpoint_tag) == filters.tag).exists(),
            )
        )
    if filters.source_client is not None:
        checkpoint_filters.append(Checkpoint.source_client == filters.source_client)
    if filters.source_session_id is not None:
        checkpoint_filters.append(Checkpoint.source_session_id == filters.source_session_id)
    if checkpoint_filters:
        conditions.append(_matching_checkpoint_exists(WorkItem.id, *checkpoint_filters))

    query = (filters.q or "").strip()
    semantic_search = filters.semantic and bool(query)
    sort_ordering = {
        "updated": [WorkItem.updated_at.desc(), WorkItem.id.desc()],
        "created": [WorkItem.created_at.desc(), WorkItem.id.desc()],
        "priority": [WorkItem.priority.desc(), WorkItem.updated_at.desc(), WorkItem.id.desc()],
    }[filters.sort]
    lexical_match = None
    ordering = []
    if query:
        terms = func.plainto_tsquery("english", query)
        work_full_text = WorkItem.search_vector.bool_op("@@")(terms)
        work_literals = [
            WorkItem.title,
            WorkItem.summary,
            cast(WorkItem.id, String),
        ]
        checkpoint_match = _matching_checkpoint_exists(
            WorkItem.id,
            or_(
                Checkpoint.search_vector.bool_op("@@")(terms),
                Checkpoint.prompt.icontains(query, autoescape=True),
                cast(Checkpoint.id, String).icontains(query, autoescape=True),
                Checkpoint.source_client.icontains(query, autoescape=True),
                Checkpoint.source_session_id.icontains(query, autoescape=True),
                Checkpoint.source_model.icontains(query, autoescape=True),
                Checkpoint.source_session_url.icontains(query, autoescape=True),
                Checkpoint.repository_branch.icontains(query, autoescape=True),
                Checkpoint.verified_against.icontains(query, autoescape=True),
                func.array_to_string(Checkpoint.tags, " ").icontains(query, autoescape=True),
            ),
        )
        lexical_match = or_(
            work_full_text,
            *(field.icontains(query, autoescape=True) for field in work_literals),
            checkpoint_match,
        )
        if not semantic_search:
            conditions.append(lexical_match)
        checkpoint_rank = (
            select(func.max(func.ts_rank_cd(Checkpoint.search_vector, terms, 32)))
            .where(Checkpoint.work_item_id == WorkItem.id)
            .scalar_subquery()
        )
        ordering.append(
            func.greatest(
                func.ts_rank_cd(WorkItem.search_vector, terms, 32),
                func.coalesce(checkpoint_rank, 0.0),
            ).desc()
        )
    ordering.extend(sort_ordering)

    if semantic_search:
        lexical_ids = list(
            database.scalars(
                select(WorkItem.id).where(*conditions, lexical_match).order_by(*ordering)
            )
        )
        candidates = list(
            database.scalars(
                select(WorkItem)
                .where(*conditions)
                .order_by(*sort_ordering)
            )
        )
        try:
            ranked = hybrid_rank(
                database,
                candidates,
                lexical_ids,
                query,
                request.app.state.semantic_embedder,
            )
            # The derived embedding cache owns an explicit search-only boundary.
            database.commit()
        except Exception as exc:
            database.rollback()
            logger.error("Semantic search failed (%s)", type(exc).__name__)
            raise ApplicationError(
                503,
                "semantic_unavailable",
                "Semantic search is unavailable. Turn it off to use lexical search.",
            ) from None
        return ranked[filters.offset : filters.offset + filters.limit], len(ranked)

    total = database.scalar(select(func.count()).select_from(WorkItem).where(*conditions)) or 0
    work_items = list(
        database.scalars(
            select(WorkItem)
            .where(*conditions)
            .order_by(*ordering)
            .limit(filters.limit)
            .offset(filters.offset)
        )
    )
    return work_items, total


@router.get("/projects", response_model=Page[ProjectRead])
def list_projects(
    filters: Annotated[ProjectListQuery, Query()], database: Database
) -> Page[ProjectRead]:
    total = database.scalar(select(func.count()).select_from(Project)) or 0
    projects = database.scalars(
        select(Project)
        .order_by(func.lower(Project.name), Project.id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return Page(
        items=[ProjectRead.model_validate(project) for project in projects],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.post("/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, database: Database) -> Project:
    project = Project(**payload.model_dump())
    database.add(project)
    try:
        database.commit()
    except IntegrityError as exc:
        database.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise conflict("slug_conflict", "A project with that slug exists.") from None
        raise
    database.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, database: Database) -> Project:
    return require_project(database, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: UUID, payload: ProjectPatch, database: Database) -> Project:
    project = require_project(database, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = datetime.now(UTC)
    database.commit()
    database.refresh(project)
    return project


@router.get("/projects/{project_id}/settings", response_model=ProjectSettingsRead)
def get_project_settings(project_id: UUID, database: Database) -> ProjectSettingsRead:
    require_project(database, project_id)
    settings = database.get(ProjectSettings, project_id)
    return ProjectSettingsRead(
        project_id=project_id,
        recall_pointer_template=(
            settings.recall_pointer_template if settings is not None else None
        ),
    )


@router.patch("/projects/{project_id}/settings", response_model=ProjectSettingsRead)
def update_project_settings(
    project_id: UUID,
    payload: ProjectSettingsPatch,
    database: Database,
) -> ProjectSettingsRead:
    require_project(database, project_id, lock=True)
    settings = database.get(ProjectSettings, project_id)
    if payload.recall_pointer_template is None:
        if settings is not None:
            database.delete(settings)
    elif settings is None:
        database.add(
            ProjectSettings(
                project_id=project_id,
                recall_pointer_template=payload.recall_pointer_template,
            )
        )
    else:
        settings.recall_pointer_template = payload.recall_pointer_template
    database.commit()
    return ProjectSettingsRead(
        project_id=project_id,
        recall_pointer_template=payload.recall_pointer_template,
    )


@router.post("/projects/{project_id}/work-items", response_model=WorkCreation, status_code=201)
def create_work(
    project_id: UUID,
    payload: WorkItemCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "create_work", project_id, {}, payload, request, database
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    domain_payload = operation.domain_payload
    work_item, checkpoint, relationships = create_work_records(
        database, project_id, domain_payload
    )
    database.refresh(work_item)
    database.refresh(checkpoint)
    for relationship in relationships:
        database.refresh(relationship)
    result = WorkCreation(
        work_item=WorkItemRead.model_validate(work_item),
        initial_checkpoint=checkpoint_read(checkpoint),
        initial_relationships=[relationship_edge(item) for item in relationships],
    )
    completed = complete_client_operation(
        database, operation, result, mutation_applied=True
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get(
    "/projects/{project_id}/work-items",
    response_model=Page[WorkSummary | HierarchySummary | WorkSummaryMinimal],
)
def search_work(
    project_id: UUID,
    filters: Annotated[WorkItemListQuery, Query()],
    request: Request,
    database: Database,
) -> Page[WorkSummary | HierarchySummary | WorkSummaryMinimal]:
    if filters.view == "roots":
        hierarchy_items, hierarchy_total = hierarchy_page(database, project_id, filters)
        return Page(
            items=hierarchy_items,
            total=hierarchy_total,
            limit=filters.limit,
            offset=filters.offset,
        )
    work_items, total = _search_work_rows(project_id, filters, request, database)
    if filters.view == "minimal":
        # Agent callers pay for every byte; skip the ancestor-path query too.
        return Page(
            items=minimal_work_summaries(database, work_items),
            total=total,
            limit=filters.limit,
            offset=filters.offset,
        )
    summaries = work_summaries(database, work_items)
    if (filters.q or "").strip():
        paths, truncated = ancestor_paths(
            database, project_id, [work_item.id for work_item in work_items]
        )
        for summary in summaries:
            summary.ancestor_path = paths.get(summary.work_item.id, [])
            summary.ancestor_path_truncated = summary.work_item.id in truncated
    return Page(
        items=summaries,
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/projects/{project_id}/ready-work", response_model=ReadyWorkPage)
def list_ready_work(
    project_id: UUID,
    filters: Annotated[ReadyWorkListQuery, Query()],
    database: Database,
) -> ReadyWorkPage:
    """List advisory ready pointers; claim-time validation remains authoritative."""
    return ready_work_page(database, project_id, filters)


@router.post(
    "/projects/{project_id}/relationships",
    response_model=RelationshipCreationResult,
)
def add_relationship(
    project_id: UUID,
    payload: RelationshipCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "add_relationship", project_id, {}, payload, request, database
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    result = add_relationship_record(database, project_id, operation.domain_payload)
    completed = complete_client_operation(
        database, operation, result, mutation_applied=result.created
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get(
    "/projects/{project_id}/relationships/{relationship_id}",
    response_model=RelationshipEdgeRead,
)
def get_relationship(
    project_id: UUID,
    relationship_id: UUID,
    database: Database,
) -> RelationshipEdgeRead:
    return relationship_edge(require_relationship(database, project_id, relationship_id))


@router.delete(
    "/projects/{project_id}/relationships/{relationship_id}",
    response_model=RelationshipRemovalResult,
)
def remove_relationship(
    project_id: UUID,
    relationship_id: UUID,
    request: Request,
    database: Database,
    payload: RelationshipRemovalCreate | None = None,
) -> JSONResponse:
    wire_payload = payload or RelationshipRemovalCreate()
    operation = _reserve_registered_operation(
        "remove_relationship",
        project_id,
        {"relationship_id": relationship_id},
        wire_payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    result = remove_relationship_record(
        database,
        project_id,
        relationship_id,
        operation.domain_payload.actor,
    )
    completed = complete_client_operation(
        database, operation, result, mutation_applied=result.removed
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/relationships",
    response_model=Page[AdjacentRelationshipRead],
)
def list_relationships(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[RelationshipListQuery, Query()],
    database: Database,
) -> Page[AdjacentRelationshipRead]:
    items, total = list_adjacent_relationships(
        database,
        project_id,
        work_item_id,
        filters,
    )
    return Page(
        items=items,
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/children",
    response_model=Page[HierarchySummary],
)
def list_children(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[ChildrenListQuery, Query()],
    database: Database,
) -> Page[HierarchySummary]:
    items, total = hierarchy_page(
        database,
        project_id,
        filters,
        parent_work_item_id=work_item_id,
    )
    return Page(
        items=items,
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    response_model=Page[CheckpointRead],
)
def list_checkpoints(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[CheckpointListQuery, Query()],
    database: Database,
) -> Page[CheckpointRead]:
    work_item = require_work_item(database, project_id, work_item_id)
    condition = Checkpoint.work_item_id == work_item.id
    total = database.scalar(select(func.count()).select_from(Checkpoint).where(condition)) or 0
    if filters.order == "newest":
        ordering = (Checkpoint.created_at.desc(), Checkpoint.id.desc())
    else:
        ordering = (Checkpoint.created_at, Checkpoint.id)
    checkpoints = database.scalars(
        select(Checkpoint)
        .where(condition)
        .order_by(*ordering)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return Page(
        items=[checkpoint_read(checkpoint) for checkpoint in checkpoints],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get(
    "/projects/{project_id}/human-attention",
    response_model=HumanAttentionPage,
)
def get_human_attention(
    project_id: UUID,
    filters: Annotated[HumanAttentionListQuery, Query()],
    database: Database,
) -> HumanAttentionPage:
    return list_human_attention(database, project_id, filters)


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/gates",
    response_model=HumanGatePage,
)
def get_work_gates(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[HumanGateListQuery, Query()],
    database: Database,
) -> HumanGatePage:
    return list_work_gates(database, project_id, work_item_id, filters)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/gates",
    response_model=HumanGateRead,
    status_code=201,
)
def create_human_gate(
    project_id: UUID,
    work_item_id: UUID,
    payload: HumanGateRequestCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    reject_gate_secret_echo(
        payload,
        known_secret_values=(request.app.state.settings.api_key.get_secret_value(),),
    )
    operation = _reserve_registered_operation(
        "request_human_input",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    try:
        reject_retained_gate_control_echo(database, operation.domain_payload)
    except ApplicationError:
        database.rollback()
        raise
    if not request.app.state.settings.human_gate_requests_enabled:
        database.rollback()
        raise human_gates_not_enabled()
    result = request_human_gate(
        database,
        project_id,
        work_item_id,
        operation.domain_payload,
    )
    completed = complete_client_operation(
        database,
        operation,
        result,
        mutation_applied=True,
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/resolve",
    response_model=HumanGateRead,
)
def resolve_human_gate_route(
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    payload: HumanGateResolutionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    reject_gate_secret_echo(
        payload,
        known_secret_values=(
            request.app.state.settings.api_key.get_secret_value(),
            str(gate_id),
        ),
    )
    operation = _reserve_registered_operation(
        "resolve_human_input",
        project_id,
        {"work_item_id": work_item_id, "gate_id": gate_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    try:
        reject_retained_gate_control_echo(database, operation.domain_payload)
    except ApplicationError:
        database.rollback()
        raise
    result = resolve_human_gate(
        database,
        project_id,
        work_item_id,
        gate_id,
        operation.domain_payload,
    )
    completed = complete_client_operation(
        database,
        operation,
        result,
        mutation_applied=True,
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/events",
    response_model=WorkEventPage,
)
def get_work_events(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[WorkEventListQuery, Query()],
    database: Database,
) -> WorkEventPage:
    return list_work_events(database, project_id, work_item_id, filters)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/events",
    response_model=WorkEventRead,
    status_code=201,
)
def append_event(
    project_id: UUID,
    work_item_id: UUID,
    payload: ProgressEventCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "append_event",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    event = append_progress_event(
        database,
        project_id,
        work_item_id,
        operation.domain_payload,
        bearer_key=request.app.state.settings.api_key.get_secret_value(),
    )
    completed = complete_client_operation(
        database, operation, event, mutation_applied=True
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    response_model=CheckpointRead,
    status_code=201,
)
def add_checkpoint(
    project_id: UUID,
    work_item_id: UUID,
    payload: CheckpointCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "add_checkpoint",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    domain_payload = operation.domain_payload
    work_item = require_work_item(
        database,
        project_id,
        work_item_id,
        lock=True,
    )
    checkpoint = append_checkpoint_record(database, work_item, domain_payload)
    database.refresh(checkpoint)
    completed = complete_client_operation(
        database,
        operation,
        checkpoint_read(checkpoint),
        mutation_applied=True,
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get("/projects/{project_id}/work-items/{work_item_id}/context", response_model=WorkContext)
def recall_work(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[WorkContextQuery, Query()],
    database: Database,
) -> WorkContext:
    return assemble_work_context(
        database,
        project_id,
        work_item_id,
        filters.recent_limit,
        filters.recent_event_limit,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/context",
    response_model=WorkContext,
)
def review_human_gate_context(
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    filters: Annotated[WorkContextQuery, Query()],
    database: Database,
) -> WorkContext:
    return assemble_work_context(
        database,
        project_id,
        work_item_id,
        filters.recent_limit,
        filters.recent_event_limit,
        focus_gate_id=gate_id,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/complete",
    response_model=WorkCompletionRead,
)
def complete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkCompletionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "complete_work",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    domain_payload = operation.domain_payload
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    checkpoint = complete_work_record(
        database,
        work_item,
        domain_payload.expected_version,
        domain_payload.checkpoint,
        domain_payload.lease_token,
    )
    database.refresh(work_item)
    database.refresh(checkpoint)
    result = WorkCompletionRead(
        work_item=WorkItemRead.model_validate(work_item),
        checkpoint=checkpoint_read(checkpoint),
    )
    completed = complete_client_operation(
        database, operation, result, mutation_applied=True
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/delete",
    response_model=WorkDeletionRead,
)
def delete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkDeletionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "delete_work",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    domain_payload = operation.domain_payload
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    delete_work_record(
        database,
        work_item,
        domain_payload.expected_version,
        domain_payload.lease_token,
        domain_payload.actor,
    )
    result = WorkDeletionRead(
        project_id=project_id,
        work_item_id=work_item_id,
        version=work_item.version,
    )
    completed = complete_client_operation(
        database, operation, result, mutation_applied=True
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.get("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemRead)
def get_work(project_id: UUID, work_item_id: UUID, database: Database) -> WorkItem:
    return require_work_item(database, project_id, work_item_id)


@router.patch("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemRead)
def update_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkItemPatch,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "update_work",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    update_work_record(database, work_item, operation.domain_payload)
    database.refresh(work_item)
    completed = complete_client_operation(
        database,
        operation,
        WorkItemRead.model_validate(work_item),
        mutation_applied=True,
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@router.post("/projects/{project_id}/work-items/{work_item_id}/defer", response_model=WorkItemRead)
def defer_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkDeferralCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    """Human dashboard action; intentionally absent from the agent MCP surface."""
    operation = _reserve_registered_operation(
        "defer_work",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    defer_work_record(database, work_item, operation.domain_payload)
    database.refresh(work_item)
    completed = complete_client_operation(
        database,
        operation,
        WorkItemRead.model_validate(work_item),
        mutation_applied=True,
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


@sync_router.websocket("/sync")
async def sync_dashboard(websocket: WebSocket) -> None:
    """Stream data-free invalidations to browsers from an allowed dashboard origin."""
    settings: Settings = websocket.app.state.settings
    if websocket.headers.get("origin") not in settings.allowed_dashboard_origins:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    hub: LiveSyncHub = websocket.app.state.live_sync_hub
    await hub.connect(websocket)
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
    finally:
        hub.disconnect(websocket)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/claim",
    response_model=ClaimReceipt,
    dependencies=[Depends(reject_lease_operation_query)],
)
def claim_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkClaimCreate,
    request: Request,
    database: Database,
) -> ClaimReceipt:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    receipt = claim_lease_record(
        database,
        work_item,
        payload,
        request.app.state.settings.lease_ttl_seconds,
    )
    database.commit()
    return receipt


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/claim-and-recall",
    response_model=ClaimAndRecall,
    dependencies=[Depends(reject_lease_operation_query)],
)
def claim_and_recall(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkClaimCreate,
    request: Request,
    database: Database,
) -> ClaimAndRecall:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    receipt = claim_lease_record(
        database,
        work_item,
        payload,
        request.app.state.settings.lease_ttl_seconds,
    )
    context = assemble_work_context(database, project_id, work_item_id, recent_limit=5)
    database.commit()
    return ClaimAndRecall(lease=receipt, context=context)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/renew-claim",
    response_model=ClaimReceipt,
    dependencies=[Depends(reject_lease_operation_query)],
)
def renew_claim(
    project_id: UUID,
    work_item_id: UUID,
    payload: LeaseTokenCreate,
    request: Request,
    database: Database,
) -> ClaimReceipt:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    receipt = renew_lease_record(
        database,
        work_item,
        payload.lease_token,
        request.app.state.settings.lease_ttl_seconds,
    )
    database.commit()
    return receipt


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/release-claim",
    response_model=ReleaseResult,
    dependencies=[Depends(reject_lease_operation_query)],
)
def release_claim(
    project_id: UUID,
    work_item_id: UUID,
    payload: LeaseReleaseCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    operation = _reserve_registered_operation(
        "release_claim",
        project_id,
        {"work_item_id": work_item_id},
        payload,
        request,
        database,
    )
    if isinstance(operation, ReplayedOperation):
        database.commit()
        _record_successful_operation(request, operation)
        return operation.response
    domain_payload = operation.domain_payload
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    result = release_lease_record(
        database, work_item, domain_payload.lease_token, domain_payload.actor
    )
    completed = complete_client_operation(
        database, operation, result, mutation_applied=result.released
    )
    database.commit()
    _record_successful_operation(request, completed)
    return completed.response


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    semantic_embedder: Embedder | None = None,
) -> FastAPI:
    config = settings if settings is not None else Settings()
    connection_pool = engine if engine is not None else build_engine(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if engine is None:
            connection_pool.dispose()

    app = FastAPI(
        title="Mnemonic API",
        version="0.2.0",
        description="Durable project-scoped work with immutable agent checkpoints.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.session_factory = build_session_factory(connection_pool)
    app.state.semantic_embedder = semantic_embedder or FastembedEmbedder()
    app.state.live_sync_hub = LiveSyncHub()
    app.include_router(router)
    app.include_router(sync_router)

    @app.middleware("http")
    async def authenticate_rest_before_routing(request: Request, call_next):
        path = request.url.path
        if (
            (path == "/api/v1" or path.startswith("/api/v1/"))
            and not _request_has_valid_bearer(request)
        ):
            return _unauthenticated_response()
        return await call_next(request)

    @app.middleware("http")
    async def broadcast_successful_mutations(request: Request, call_next):
        response = await call_next(request)
        event = mutation_event(request.method, request.url.path)
        mutation_applied = getattr(request.state, _MUTATION_APPLIED_STATE, None)
        operation_kind = getattr(request.state, _CLIENT_OPERATION_KIND_STATE, None)
        operation_outcome = getattr(
            request.state, _CLIENT_OPERATION_OUTCOME_STATE, None
        )
        if operation_kind is not None:
            if operation_outcome is None and response.status_code >= 500:
                operation_outcome = "unavailable"
            if operation_outcome is not None:
                logger.info(
                    "Client operation outcome kind=%s outcome=%s",
                    operation_kind,
                    operation_outcome,
                )
        if (
            event is not None
            and 200 <= response.status_code < 300
            and mutation_applied is not False
        ):
            await app.state.live_sync_hub.publish(event)
        return response

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(database: Database) -> JSONResponse:
        try:
            database.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ready"})

    @app.exception_handler(SQLAlchemyError)
    async def database_failure(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("Database operation failed (%s)", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "database_unavailable",
                    "message": "Database operation unavailable.",
                    "context": {},
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = _public_validation_errors(exc.errors())
        return JSONResponse(status_code=422, content={"detail": errors})

    return app
