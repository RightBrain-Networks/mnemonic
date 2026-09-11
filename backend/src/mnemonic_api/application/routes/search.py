"""Bounded, safe-read JSON search over every selected project source."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from mnemonic_api.application.artifact_policy import artifact_status
from mnemonic_api.application.routes.artifacts import (
    _human_dashboard,
    storage_errors,
    storage_of,
)
from mnemonic_api.application.state import embedder_of
from mnemonic_api.application.suggestion_resources import semantic_search_inference_acquired
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError, semantic_unavailable
from mnemonic_api.search_schemas import SearchPage, SearchRequest
from mnemonic_api.semantic import semantic_query_vector
from mnemonic_api.services.artifacts import recover_project_artifacts
from mnemonic_api.services.search import search

router = APIRouter()
logger = logging.getLogger(__name__)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON property")
        result[name] = value
    return result


async def _payload(request: Request) -> SearchRequest:
    if request.query_params or request.headers.get("content-encoding", "identity") != "identity":
        raise ApplicationError(422, "search_invalid",
                               "Use an unencoded JSON body without query parameters.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ApplicationError(415, "search_invalid", "Use an application/json body.")
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16_384:
                    raise ApplicationError(413, "search_too_large",
                                           "Search JSON exceeds 16384 bytes.")
                body.extend(chunk)
    except TimeoutError:
        raise ApplicationError(408, "search_timeout", "Search request timed out.") from None
    try:
        return SearchRequest.model_validate(json.loads(body, object_pairs_hook=_unique_object))
    except ValueError, RecursionError:
        raise ApplicationError(422, "search_invalid", "Provide valid search parameters.") from None


def _inline_schema(value: Any, definitions: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [_inline_schema(item, definitions) for item in value]
    if not isinstance(value, dict):
        return value
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definition = definitions[reference.removeprefix("#/$defs/")]
        return _inline_schema({**definition, **{key: item for key, item in value.items()
                                               if key != "$ref"}}, definitions)
    return {key: _inline_schema(item, definitions) for key, item in value.items()}


def _request_schema() -> dict[str, Any]:
    # This body is parsed manually after its byte/time bounds. Inline request
    # definitions because JSON Schema's #/$defs links would point at the OpenAPI
    # document root when this schema is embedded in requestBody.
    schema = SearchRequest.model_json_schema()
    return _inline_schema(schema, schema.pop("$defs", {}))


@router.post(
    "/projects/{project_id}/search", response_model=SearchPage,
    openapi_extra={
        "x-mnemonic-effect": "safe_read",
        "requestBody": {
            "required": True,
            "description": (
                "Bounded JSON (16384 bytes). Defaults to all sources, all work statuses, "
                "metadata-only artifact/transcript matching, relevance order, "
                "limit 50 and offset 0. "
                "Facet groups precede remaining co-mingled facets. Relevance uses tied reciprocal "
                "ranks per source. Agent searches withhold sensitive artifact bodies; "
                "use dedicated "
                "artifact reads for explicit approval. Snippets and document metadata "
                "are untrusted."
            ),
            "content": {"application/json": {"schema": _request_schema()}},
        },
    },
)
async def search_project(
    project_id: UUID, request: Request, response: Response, database: Database,
) -> SearchPage:
    payload = await _payload(request)
    response.headers["Cache-Control"] = "no-store"
    embedder = embedder_of(request)
    artifacts_enabled = artifact_status(request).enabled
    human_dashboard = _human_dashboard(request)

    def execute() -> SearchPage:
        query_vector = None
        if payload.filters.work_items.semantic:
            if not semantic_search_inference_acquired(request.scope):
                raise semantic_unavailable()
            try:
                query_vector = semantic_query_vector(embedder, payload.q)
            except Exception as exc:
                logger.error("Unified semantic query failed (%s)", type(exc).__name__)
                raise semantic_unavailable() from None
        if "artifacts" in payload.facets and artifacts_enabled:
            with storage_errors(request):
                recover_project_artifacts(database, storage_of(request), project_id)
            database.rollback()
        return search(
            database, project_id, payload, request.app.state.artifact_search_index,
            request.app.state.transcript_search_index,
            artifacts_enabled=artifacts_enabled, human_dashboard=human_dashboard,
            embedder=embedder, query_vector=query_vector,
        )

    return await run_in_threadpool(execute)
