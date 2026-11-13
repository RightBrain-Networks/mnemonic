"""Safe-read JSON endpoint for bounded project activity deltas."""

import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from mnemonic_api.activity_query_schemas import QueryActivityPage, QueryActivityRequest
from mnemonic_api.application.routes.search import _inline_schema, _unique_object
from mnemonic_api.application.validation import raise_reviewed_body_validation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError
from mnemonic_api.services.activity_query import query_activity

router = APIRouter()


def _request_schema():
    schema = QueryActivityRequest.model_json_schema()
    return _inline_schema(schema, schema.pop("$defs", {}))


async def _payload(request: Request) -> QueryActivityRequest:
    if request.query_params or request.headers.get("content-encoding", "identity") != "identity":
        raise ApplicationError(422, "activity_query_invalid",
                               "Use an unencoded JSON body without query parameters.")
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise ApplicationError(415, "activity_query_invalid", "Use an application/json body.")
    body = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(body) + len(chunk) > 16384:
                    raise ApplicationError(413, "activity_query_too_large",
                                           "Activity query JSON exceeds 16384 bytes.")
                body.extend(chunk)
    except TimeoutError:
        raise ApplicationError(408, "activity_query_timeout", "Activity query timed out.") from None
    try:
        value = json.loads(body, object_pairs_hook=_unique_object)
        return QueryActivityRequest.model_validate(value)
    except (ValueError, RecursionError) as exc:
        raise_reviewed_body_validation(exc)
        raise ApplicationError(422, "activity_query_invalid",
                               "Provide valid activity parameters.") from None


def _read(database: Database, project_id: UUID, payload: QueryActivityRequest) -> QueryActivityPage:
    begin_coherent_read(database)
    return query_activity(database, project_id, payload)


@router.post(
    "/projects/{project_id}/activity/query", response_model=QueryActivityPage,
    openapi_extra={"x-mnemonic-effect": "safe_read", "requestBody": {
        "required": True, "content": {"application/json": {"schema": _request_schema()}},
    }},
)
async def query_project_activity(
    project_id: UUID, request: Request, database: Database,
) -> QueryActivityPage:
    payload = await _payload(request)
    return await run_in_threadpool(_read, database, project_id, payload)
