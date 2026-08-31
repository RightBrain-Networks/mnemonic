"""Authenticated, project-scoped REST operations for durable hand-off prompts."""

import logging
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, defer

from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory, get_session
from mnemonic_api.models import Handoff, Project
from mnemonic_api.schemas import (
    HandoffCreate,
    HandoffListQuery,
    HandoffPatch,
    HandoffRead,
    HandoffSummary,
    Page,
    ProjectCreate,
    ProjectListQuery,
    ProjectPatch,
    ProjectRead,
)
from mnemonic_api.semantic import Embedder, FastembedEmbedder, hybrid_rank

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


router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])


def require_project(database: Session, project_id: UUID) -> Project:
    project = database.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_handoff(
    database: Session, project_id: UUID, handoff_id: UUID, *, lock: bool = False
) -> Handoff:
    statement = select(Handoff).where(
        Handoff.id == handoff_id,
        Handoff.project_id == project_id,
        Handoff.deleted_at.is_(None),
    )
    if lock:
        # The lock and version check share the mutation's transaction. Two writers
        # reading the same version cannot both pass after either one commits.
        statement = statement.with_for_update()
    handoff = database.scalar(statement)
    if handoff is None:
        raise HTTPException(status_code=404, detail="Hand-off not found")
    return handoff


def require_version(handoff: Handoff, expected_version: int) -> None:
    if handoff.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail="This hand-off changed. Recall it again before editing or deleting.",
        )


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
        # The database unique constraint arbitrates simultaneous same-slug writes.
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise HTTPException(status_code=409, detail="A project with that slug exists") from None
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


@router.post("/projects/{project_id}/handoffs", response_model=HandoffRead, status_code=201)
def save_handoff(project_id: UUID, payload: HandoffCreate, database: Database) -> Handoff:
    require_project(database, project_id)
    handoff = Handoff(project_id=project_id, **payload.model_dump())
    database.add(handoff)
    database.commit()
    database.refresh(handoff)
    return handoff


@router.get("/projects/{project_id}/handoffs", response_model=Page[HandoffSummary])
def search_handoffs(
    project_id: UUID,
    filters: Annotated[HandoffListQuery, Query()],
    request: Request,
    database: Database,
) -> Page[HandoffSummary]:
    require_project(database, project_id)
    conditions = [Handoff.project_id == project_id, Handoff.deleted_at.is_(None)]
    if filters.status != "all":
        conditions.append(Handoff.status == filters.status)
    if filters.tag is not None:
        conditions.append(Handoff.tags.contains([filters.tag]))
    if filters.source_client is not None:
        conditions.append(Handoff.source_client == filters.source_client)
    if filters.source_session_id is not None:
        conditions.append(Handoff.source_session_id == filters.source_session_id)

    query = (filters.q or "").strip()
    semantic_search = filters.semantic and bool(query)
    ordering = []
    lexical_match = None
    if query:
        terms = func.plainto_tsquery("english", query)
        full_text_match = Handoff.search_vector.bool_op("@@")(terms)
        # Identifiers and paths are often poor language tokens. The separate
        # escaped substring path preserves those searches without treating %, _,
        # quotes, or backslashes as SQL syntax or LIKE wildcards.
        literal_fields = [
            Handoff.title,
            Handoff.summary,
            Handoff.prompt,
            cast(Handoff.id, String),
            Handoff.source_client,
            Handoff.source_session_id,
            Handoff.source_model,
            Handoff.source_session_url,
            Handoff.repository_branch,
            Handoff.verified_against,
            func.array_to_string(Handoff.tags, " "),
        ]
        lexical_match = or_(
            full_text_match,
            *(field.icontains(query, autoescape=True) for field in literal_fields),
        )
        if not semantic_search:
            conditions.append(lexical_match)
        ordering.append(func.ts_rank_cd(Handoff.search_vector, terms, 32).desc())
    ordering.extend([Handoff.updated_at.desc(), Handoff.id.desc()])

    if semantic_search:
        lexical_ids = list(
            database.scalars(
                select(Handoff.id).where(*conditions, lexical_match).order_by(*ordering)
            )
        )
        candidates = list(
            database.scalars(
                select(Handoff)
                .options(defer(Handoff.source_metadata), defer(Handoff.search_vector))
                .where(*conditions)
                .order_by(Handoff.updated_at.desc(), Handoff.id.desc())
            )
        )
        try:
            ranked = hybrid_rank(
                database, candidates, lexical_ids, query, request.app.state.semantic_embedder
            )
        except Exception as exc:
            database.rollback()
            logger.error("Semantic search failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=503,
                detail="Semantic search is unavailable. Turn it off to use lexical search.",
            ) from None
        page = ranked[filters.offset : filters.offset + filters.limit]
        return Page(
            items=[HandoffSummary.model_validate(handoff) for handoff in page],
            total=len(ranked),
            limit=filters.limit,
            offset=filters.offset,
        )

    total = database.scalar(select(func.count()).select_from(Handoff).where(*conditions)) or 0
    handoffs = database.scalars(
        select(Handoff)
        .options(
            defer(Handoff.prompt), defer(Handoff.source_metadata), defer(Handoff.search_vector)
        )
        .where(*conditions)
        .order_by(*ordering)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return Page(
        items=[HandoffSummary.model_validate(handoff) for handoff in handoffs],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/projects/{project_id}/handoffs/{handoff_id}", response_model=HandoffRead)
def recall_handoff(project_id: UUID, handoff_id: UUID, database: Database) -> Handoff:
    return require_handoff(database, project_id, handoff_id)


@router.patch("/projects/{project_id}/handoffs/{handoff_id}", response_model=HandoffRead)
def update_handoff(
    project_id: UUID, handoff_id: UUID, payload: HandoffPatch, database: Database
) -> Handoff:
    handoff = require_handoff(database, project_id, handoff_id, lock=True)
    require_version(handoff, payload.expected_version)
    for field, value in payload.model_dump(
        exclude_unset=True, exclude={"expected_version"}
    ).items():
        setattr(handoff, field, value)
    handoff.version += 1
    handoff.updated_at = datetime.now(UTC)
    database.commit()
    database.refresh(handoff)
    return handoff


@router.delete("/projects/{project_id}/handoffs/{handoff_id}", status_code=204)
def delete_handoff(
    project_id: UUID,
    handoff_id: UUID,
    expected_version: Annotated[int, Query(ge=1)],
    database: Database,
) -> Response:
    handoff = require_handoff(database, project_id, handoff_id, lock=True)
    require_version(handoff, expected_version)
    handoff.deleted_at = datetime.now(UTC)
    handoff.updated_at = handoff.deleted_at
    handoff.version += 1
    database.commit()
    return Response(status_code=204)


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
        version="0.1.0",
        description="Durable agent-authored hand-off prompts, scoped to projects.",
        lifespan=lifespan,
    )
    app.state.settings = config
    app.state.session_factory = build_session_factory(connection_pool)
    app.state.semantic_embedder = semantic_embedder or FastembedEmbedder()
    app.include_router(router)

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
        # Exception messages can contain connection strings or row contents.
        logger.error("Database operation failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database operation unavailable"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo full prompts, metadata, or non-JSON values in error bodies.
        # NaN/surrogate input otherwise breaks the default error serializer even
        # though validation correctly rejected it.
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
