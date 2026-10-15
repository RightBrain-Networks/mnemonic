"""Production browser middleware with deterministic domain wire-boundary fixtures.

Only this isolated nginx Compose overlay imports this module. Controlled routes
precede domain routing after the normal factory installs its full middleware;
backup forwarding, runtime configuration and WebSocket sync stay production code.
Real domain persistence and worker restore semantics are covered by Playwright.
"""

import asyncio
import gzip
import hashlib
import json

from encoding_upstream import HISTORY_MAX_BYTES, SENSITIVE_MARKER, padded_json
from fastapi.routing import APIRoute
from mnemonic_api.main import create_app as production_app
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

RAW_PREFIX = b"raw-browser-stream\x00\xff\n"
RAW_SUFFIX = b"stream-complete\x00\xfe\n"


async def evidence(request: Request) -> Response:
    cursor = request.query_params.get("cursor", "")
    if cursor in {"gzip-success", "gzip-error"}:
        return Response(
            gzip.compress(json.dumps({"detail": SENSITIVE_MARKER}).encode()),
            status_code=200 if cursor == "gzip-success" else 503,
            media_type="application/json",
            headers={"Content-Encoding": "gzip"},
        )
    size = HISTORY_MAX_BYTES + (cursor == "identity-max-plus-one")
    body = (
        padded_json(size)
        if cursor.startswith("identity-limit") or cursor == "identity-max-plus-one"
        else b'{"completion_event_id":9007199254740993}'
    )
    return Response(
        body,
        media_type="application/json",
        headers=({} if cursor == "identity-absent" else {"Content-Encoding": "identity"}),
    )


async def completion(request: Request) -> Response:
    return JSONResponse({"accepted_body_bytes": len(await request.body())})


async def upload(request: Request) -> Response:
    metadata = request.headers.get("x-artifact-metadata", "")
    count = 0
    digest = hashlib.sha256()
    async for chunk in request.stream():
        count += len(chunk)
        digest.update(chunk)
        request.app.state.fixture_upload_bytes = count
    if len(metadata) == 16384:
        return JSONResponse({"metadata_bytes": len(metadata)})
    return JSONResponse({"body_bytes": count, "sha256": digest.hexdigest()})


async def upload_progress(request: Request) -> Response:
    return JSONResponse({"body_bytes": getattr(request.app.state, "fixture_upload_bytes", 0)})


async def download(request: Request) -> Response:
    async def chunks():
        yield RAW_PREFIX
        await asyncio.sleep(2)
        yield RAW_SUFFIX

    headers = {"Content-Disposition": 'attachment; filename="transport.bin"'}
    if request.url.path.startswith("/api/artifacts/"):
        headers["Content-Length"] = str(len(RAW_PREFIX) + len(RAW_SUFFIX))
    return StreamingResponse(chunks(), media_type="application/octet-stream", headers=headers)


def create_app():
    app = production_app()
    project = "/projects/{project_id}"
    work = project + "/work-items/{work_id}"
    fixtures = [
        ("/api/mnemonic" + work + "/completion-evidence", evidence, "GET"),
        ("/api/mnemonic" + work + "/complete", completion, "POST"),
        ("/api/artifacts" + project + "/artifacts", upload, "POST"),
        ("/api/artifacts" + project + "/artifacts/{artifact_id}", upload_progress, "GET"),
        ("/api/artifacts" + project + "/artifacts/{artifact_id}/content", download, "GET"),
        ("/api/transcripts" + project + "/transcripts/{transcript_id}/content", download, "GET"),
    ]
    app.router.routes[0:0] = [
        APIRoute(path, handler, methods=[method], include_in_schema=False)
        for path, handler, method in fixtures
    ]
    return app
