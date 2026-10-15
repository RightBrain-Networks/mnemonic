"""The dashboard JSON wire contract, applied after domain execution.

Invalid responses are uncertain failures (502), even when the domain committed.
Never parse/re-serialize successful bytes: completion event IDs exceed JS's
safe integer range, and the original UTF-8/identity representation must survive.
"""

import json
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import Message

from mnemonic_api.application.browser_prompt_responses import validate_prompt_response

RESPONSE_HEADERS = {
    "Cache-Control": "no-store, max-age=0, no-transform",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
}
INCOMPLETE_RESPONSE = "Mnemonic's API returned an incomplete response."
UNREACHABLE = (
    "Cannot reach Mnemonic's API. Check that the API and database containers are running."
)


def evidence_route(path: str, method: str) -> bool:
    return method == "GET" and path.endswith("/completion-evidence")


def failure(status: int, detail: str, *, identity: bool = False) -> Response:
    headers = {**RESPONSE_HEADERS, **({"Content-Encoding": "identity"} if identity else {})}
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def response_limit(path: str, method: str) -> int | None:
    if evidence_route(path, method):
        return 3_145_728
    parts = path.split("/")
    if "prompts" in parts or parts[-1] == "settings":
        return 4_194_304
    if parts[-1] == "priority-rubric":
        return 2_097_152
    if parts[-1] == "search" and method == "POST":
        return 16_777_216
    return _library_response_limit(parts, method)


def _library_response_limit(parts: list[str], method: str) -> int | None:
    last = parts[-1]
    if last in {"code-reviews", "work-agent-follow-ups", "activity"}:
        return 524_288
    if len(parts) > 1 and parts[-2] == "code-reviews":
        return 786_432
    if "agent-follow-ups" in parts:
        return 1_048_576 if last == "answer" else 65_536
    if last == "report-follow-ups":
        return 262_144
    if "job-completion-reports" not in parts:
        return None
    limits = {"job-completion-reports": 2_097_152, "count": 1_024, "dismiss": 16_384}
    if last == "follow-ups":
        return 262_144 if method == "GET" else 1_048_576
    return limits.get(last, 4_194_304)


def retry_after(status: int, headers: Headers) -> str | None:
    value = headers.get("retry-after")
    if status != 429 or not value or len(value) > 128:
        return None
    if re.fullmatch(r"[0-9]+", value):
        return value
    try:
        parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("Non-JSON constant")


@dataclass
class BrowserResponse:
    path: str
    method: str
    status: int = 502
    headers: Headers = field(default_factory=Headers)
    body: bytearray = field(default_factory=bytearray)
    invalid: bool = False
    complete: bool = False

    async def capture(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            raw = Headers(raw=message.get("headers", []))
            self.headers = Headers({name: ", ".join(raw.getlist(name)) for name in raw})
            limit = response_limit(self.path, self.method)
            declared = self.headers.get("content-length", "")
            self.invalid |= limit is not None and declared.isdecimal() and int(declared) > limit
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            limit = response_limit(self.path, self.method)
            self.invalid |= limit is not None and len(self.body) + len(chunk) > limit
            if not self.invalid:
                self.body.extend(chunk)
            self.complete = not message.get("more_body", False)

    def response(self) -> Response:
        identity = evidence_route(self.path, self.method)
        error = self._error(identity)
        if error is not None:
            return failure(502, error, identity=identity)
        headers = dict(RESPONSE_HEADERS)
        if identity:
            headers["Content-Encoding"] = "identity"
        if self.status != 204:
            headers["Content-Type"] = "application/json"
        retry = retry_after(self.status, self.headers)
        if retry is not None:
            headers["Retry-After"] = retry
        return Response(bytes(self.body), status_code=self.status, headers=headers)

    def _error(self, identity: bool) -> str | None:
        if self.invalid or not self.complete:
            return INCOMPLETE_RESPONSE
        if identity and self.headers.get("content-encoding", "identity").lower() != "identity":
            return INCOMPLETE_RESPONSE
        if 300 <= self.status < 400:
            return "Mnemonic's API returned an unexpected redirect."
        if self.status == 204:
            return None
        if "application/json" not in self.headers.get("content-type", ""):
            return "Mnemonic's API returned an unexpected response."
        try:
            value = json.loads(self.body.decode("utf-8-sig"), parse_constant=_reject_constant)
            if 200 <= self.status < 300:
                validate_prompt_response(self.path, self.method, value)
        except (ValueError, TypeError, KeyError, RecursionError):
            return INCOMPLETE_RESPONSE
        return None
