"""Authenticated, project-scoped REST API for canonical work and checkpoints."""

import logging
import secrets
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
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.live_sync import LiveSyncHub, mutation_event
from mnemonic_api.models import Checkpoint, Project, WorkItem
from mnemonic_api.schemas import (
    AdjacentRelationshipRead,
    CheckpointCreate,
    CheckpointListQuery,
    CheckpointRead,
    ChildrenListQuery,
    ClaimAndRecall,
    ClaimReceipt,
    HierarchySummary,
    LeaseTokenCreate,
    Page,
    ProjectCreate,
    ProjectListQuery,
    ProjectPatch,
    ProjectRead,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipListQuery,
    RelationshipRemovalResult,
    ReleaseResult,
    WorkClaimCreate,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkContext,
    WorkContextQuery,
    WorkCreation,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkItemCreate,
    WorkItemListQuery,
    WorkItemPatch,
    WorkItemRead,
    WorkSummary,
)
from mnemonic_api.semantic import Embedder, FastembedEmbedder, hybrid_rank
from mnemonic_api.services.leases import (
    claim_lease_record,
    release_lease_record,
    renew_lease_record,
)
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
    work_summaries,
)
from mnemonic_api.services.work_items import (
    append_checkpoint_record,
    complete_work_record,
    create_work_records,
    delete_work_record,
    require_project,
    require_work_item,
    update_work_record,
)

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)
Database = Annotated[Session, Depends(get_session)]


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


def reject_lease_operation_query(request: Request) -> None:
    if request.query_params:
        _raise_query_rejection("Query parameters are not accepted for lease operations.")


# Dependency order is part of the HTTP contract: authentication must run before
# capability/query validation so unauthenticated API requests remain 401.
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(authenticate), Depends(reject_lease_token_query)],
)
sync_router = APIRouter(prefix="/api/v1")


def _matching_checkpoint_exists(work_item_id, *conditions):
    return (
        select(Checkpoint.id)
        .where(Checkpoint.work_item_id == work_item_id, *conditions)
        .exists()
    )


def _search_work_rows(
    project_id: UUID,
    filters: WorkItemListQuery,
    request: Request,
    database: Session,
) -> tuple[list[WorkItem], int]:
    require_project(database, project_id)
    conditions = [WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None)]
    if filters.status != "all":
        conditions.append(WorkItem.status == filters.status)

    checkpoint_filters = []
    if filters.tag is not None:
        checkpoint_tag = func.unnest(Checkpoint.tags).column_valued("checkpoint_tag")
        checkpoint_filters.append(
            or_(
                # Keep the indexed normalized-data fast path while allowing
                # exact migrations that preserved historical tag case.
                Checkpoint.tags.contains([filters.tag]),
                select(1)
                .where(func.lower(checkpoint_tag) == filters.tag)
                .exists(),
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
    ordering.extend([WorkItem.updated_at.desc(), WorkItem.id.desc()])

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
                .order_by(WorkItem.updated_at.desc(), WorkItem.id.desc())
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


@router.post(
    "/projects/{project_id}/work-items", response_model=WorkCreation, status_code=201
)
def create_work(
    project_id: UUID, payload: WorkItemCreate, database: Database
) -> WorkCreation:
    work_item, checkpoint, relationships = create_work_records(database, project_id, payload)
    database.commit()
    database.refresh(work_item)
    database.refresh(checkpoint)
    for relationship in relationships:
        database.refresh(relationship)
    return WorkCreation(
        work_item=WorkItemRead.model_validate(work_item),
        initial_checkpoint=checkpoint_read(checkpoint),
        initial_relationships=[relationship_edge(item) for item in relationships],
    )


@router.get(
    "/projects/{project_id}/work-items",
    response_model=Page[WorkSummary | HierarchySummary],
)
def search_work(
    project_id: UUID,
    filters: Annotated[WorkItemListQuery, Query()],
    request: Request,
    database: Database,
) -> Page[WorkSummary | HierarchySummary]:
    if filters.view == "roots":
        hierarchy_items, hierarchy_total = hierarchy_page(database, project_id, filters)
        return Page(
            items=hierarchy_items,
            total=hierarchy_total,
            limit=filters.limit,
            offset=filters.offset,
        )
    work_items, total = _search_work_rows(project_id, filters, request, database)
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


@router.post(
    "/projects/{project_id}/relationships",
    response_model=RelationshipCreationResult,
)
def add_relationship(
    project_id: UUID,
    payload: RelationshipCreate,
    database: Database,
) -> RelationshipCreationResult:
    result = add_relationship_record(database, project_id, payload)
    database.commit()
    return result


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
    database: Database,
) -> RelationshipRemovalResult:
    result = remove_relationship_record(database, project_id, relationship_id)
    database.commit()
    return result


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


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    response_model=CheckpointRead,
    status_code=201,
)
def add_checkpoint(
    project_id: UUID,
    work_item_id: UUID,
    payload: CheckpointCreate,
    database: Database,
) -> Checkpoint:
    work_item = require_work_item(
        database,
        project_id,
        work_item_id,
        lock=payload.lease_token is not None,
    )
    checkpoint = append_checkpoint_record(database, work_item, payload)
    database.commit()
    database.refresh(checkpoint)
    return checkpoint


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/context", response_model=WorkContext
)
def recall_work(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[WorkContextQuery, Query()],
    database: Database,
) -> WorkContext:
    return assemble_work_context(database, project_id, work_item_id, filters.recent_limit)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/complete",
    response_model=WorkCompletionRead,
)
def complete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkCompletionCreate,
    database: Database,
) -> WorkCompletionRead:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    checkpoint = complete_work_record(
        database,
        work_item,
        payload.expected_version,
        payload.checkpoint,
        payload.lease_token,
    )
    database.commit()
    database.refresh(work_item)
    database.refresh(checkpoint)
    return WorkCompletionRead(
        work_item=WorkItemRead.model_validate(work_item),
        checkpoint=checkpoint_read(checkpoint),
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/delete",
    response_model=WorkDeletionRead,
)
def delete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkDeletionCreate,
    database: Database,
) -> WorkDeletionRead:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    delete_work_record(database, work_item, payload.expected_version, payload.lease_token)
    database.commit()
    return WorkDeletionRead(
        project_id=project_id,
        work_item_id=work_item_id,
        version=work_item.version,
    )


@router.get("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemRead)
def get_work(project_id: UUID, work_item_id: UUID, database: Database) -> WorkItem:
    return require_work_item(database, project_id, work_item_id)


@router.patch("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkItemRead)
def update_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkItemPatch,
    database: Database,
) -> WorkItem:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    update_work_record(database, work_item, payload)
    database.commit()
    database.refresh(work_item)
    return work_item


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
    payload: LeaseTokenCreate,
    database: Database,
) -> ReleaseResult:
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    result = release_lease_record(database, work_item, payload.lease_token)
    database.commit()
    return result


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
    async def broadcast_successful_mutations(request: Request, call_next):
        response = await call_next(request)
        event = mutation_event(request.method, request.url.path)
        if event is not None and 200 <= response.status_code < 300:
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
        def safe_text(value: str) -> str:
            return value.encode("utf-8", errors="replace").decode("utf-8")

        errors = [
            {
                "type": safe_text(error["type"]),
                "loc": [
                    safe_text(part) if isinstance(part, str) else part for part in error["loc"]
                ],
                "msg": safe_text(error["msg"]),
            }
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    return app
