"""Streaming browser gateway to the independently authenticated backup worker.

The worker remains the only backup domain owner. A browser disconnect or deadline
cancels its HTTP connection; mutations are never retried because their outcome
may already be durable. Response bodies use raw identity bytes without decoding.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass

import httpx
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mnemonic_api.application.browser_backups_policy import (
    BACKUP_BROWSER_PREFIX,
    BackupAdmission,
    BackupRejection,
    admit_backup,
    admit_backup_body,
)
from mnemonic_api.application.browser_file_transport import (
    SECURITY_HEADERS,
    declared_length,
    decode_file_json,
    file_failure,
)
from mnemonic_api.application.browser_policy.values import MAX_SAFE_INTEGER

BACKUP_TIMEOUT_SECONDS = 30 * 60
BACKUP_JSON_MAX_BYTES = 4 * 1024 * 1024
FAILURE_DETAIL = (
    "The backup request could not be completed. Its outcome may be uncertain; "
    "reload and inspect the project before starting another action."
)


class _BackupInput:
    def __init__(self, receive: Receive, maximum: int, restore: bool) -> None:
        self.receive = receive
        self.maximum = maximum
        self.complete = not restore
        self.queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)

    async def pump(self) -> None:
        size = 0
        while True:
            message = await self.receive()
            if message["type"] == "http.disconnect":
                return
            if self.complete:
                continue
            size += len(message.get("body", b""))
            if size > self.maximum:
                raise BackupRejection(413, "Backup archive exceeds the configured size limit.")
            await self.queue.put(message)
            self.complete = not message.get("more_body", False)

    async def stream(self) -> AsyncIterator[bytes]:
        while True:
            message = await self.queue.get()
            if chunk := message.get("body", b""):
                yield chunk
            if not message.get("more_body", False):
                return


def _admit_response(response: httpx.Response) -> None:
    if 300 <= response.status_code < 400:
        raise BackupRejection(502, "The backup service returned an unexpected redirect.")
    encoding = response.headers.get("content-encoding")
    if encoding and encoding.lower() != "identity":
        raise BackupRejection(502, "The backup service returned an encoded response.")


async def _json_response(response: httpx.Response) -> Response:
    if response.status_code in {204, 205, 304}:
        raise ValueError("Backup action returned a bodyless status")
    if "application/json" not in response.headers.get("content-type", ""):
        raise BackupRejection(502, "The backup service returned an unexpected response.")
    declared_length(Headers(response.headers), BACKUP_JSON_MAX_BYTES)
    body = bytearray()
    async for chunk in response.aiter_raw():
        if len(body) + len(chunk) > BACKUP_JSON_MAX_BYTES:
            raise ValueError("Oversized backup response")
        body.extend(chunk)
    decode_file_json(bytes(body))
    return Response(bytes(body), status_code=response.status_code,
                    media_type="application/json", headers=SECURITY_HEADERS)


def _download_headers(response: httpx.Response, path: str) -> tuple[int, dict[str, str]]:
    try:
        length = declared_length(Headers(response.headers), MAX_SAFE_INTEGER)
    except ValueError:
        raise BackupRejection(
            502, "The backup service returned an invalid archive response.") from None
    if response.status_code != 200 or length is None or length < 1:
        raise BackupRejection(502, "The backup service returned an invalid archive response.")
    return length, {**SECURITY_HEADERS, "Content-Type": "application/octet-stream",
                    "Content-Length": response.headers["content-length"],
                    "Content-Disposition": f'attachment; filename="{path.split("/")[-1]}"'}


@dataclass
class _BackupOutput:
    scope: Scope
    receive: Receive
    send: Send
    started: bool = False

    async def download(self, response: httpx.Response, path: str) -> None:
        length, headers = _download_headers(response, path)
        size = 0
        async for chunk in response.aiter_raw():
            size += len(chunk)
            if size > length:
                raise ValueError("Oversized backup download")
            if not self.started:
                self.started = True
                await self.send({"type": "http.response.start", "status": 200,
                                 "headers": Headers(headers).raw})
            await self.send({"type": "http.response.body", "body": chunk, "more_body": True})
        if size != length:
            raise ValueError("Incomplete backup download")
        await self.send({"type": "http.response.body", "body": b"", "more_body": False})

    async def forward(
        self, client: httpx.AsyncClient, admission: BackupAdmission, source: _BackupInput,
    ) -> None:
        headers = {"Authorization": f"Bearer {admission.token}", "Accept-Encoding": "identity"}
        content = None
        if admission.action == "restore":
            headers.update({"Content-Type": "application/octet-stream",
                            "X-Confirm-Project": admission.project_id})
            content = source.stream()
        request = client.build_request(self.scope["method"], f"{admission.url}/{admission.path}",
                                       headers=headers, content=content, timeout=None)
        # A shared client's cookie jar must never turn a worker Set-Cookie into
        # authority on a later request, even though no cookie reaches the browser.
        request.headers.pop("cookie", None)
        response = await client.send(request, stream=True, follow_redirects=False)
        try:
            _admit_response(response)
            if admission.action == "download" and response.is_success:
                await self.download(response, admission.path)
            else:
                result = await _json_response(response)
                self.started = True
                await result(self.scope, self.receive, self.send)
        finally:
            await response.aclose()


async def _empty_body(request: Request) -> None:
    async for chunk in request.stream():
        if chunk:
            raise BackupRejection(400, "This backup action does not accept a request body.")


async def _exchange(request: Request, admission: BackupAdmission, output: _BackupOutput) -> None:
    if admission.action != "restore":
        await _empty_body(request)
    source = _BackupInput(output.receive, admission.maximum, admission.action == "restore")
    transfer = asyncio.create_task(
        output.forward(request.app.state.backup_client, admission, source))
    watcher = asyncio.create_task(source.pump())
    try:
        done, _ = await asyncio.wait((transfer, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
            if transfer not in done:
                raise ClientDisconnect()
        await transfer
    finally:
        transfer.cancel()
        watcher.cancel()
        await asyncio.gather(transfer, watcher, return_exceptions=True)


class BackupBrowserMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        matches = path == BACKUP_BROWSER_PREFIX or path.startswith(BACKUP_BROWSER_PREFIX + "/")
        if scope["type"] != "http" or not matches:
            await self.app(scope, receive, send)
            return
        output = _BackupOutput(scope, receive, send)
        request = Request(scope, receive)
        try:
            admission = admit_backup(request)
            admit_backup_body(request, admission)
            async with asyncio.timeout(BACKUP_TIMEOUT_SECONDS):
                await _exchange(request, admission, output)
            return
        except ClientDisconnect:
            return
        except Exception as exc:
            if output.started:
                raise
            status, detail = (exc.status, exc.detail) if isinstance(exc, BackupRejection) else (
                502, FAILURE_DETAIL)
        with suppress(ClientDisconnect):
            await file_failure(status, detail)(scope, receive, send)
