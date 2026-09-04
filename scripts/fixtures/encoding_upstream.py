"""Controlled identity/encoded upstream for the nginx acceptance matrix."""

from __future__ import annotations

import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HOST = "0.0.0.0"
PORT = 8081
SENSITIVE_MARKER = "phase11-encoded-poison-must-not-be-reflected"
HISTORY_MAX_BYTES = 3 * 1024 * 1024


def padded_json(size: int) -> bytes:
    """Return one valid JSON value occupying exactly size identity bytes."""
    if size < 2:
        raise ValueError("JSON fixture size must fit an object")
    return b"{}" + b" " * (size - 2)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._send(200, b'{"status":"ok"}')
            return
        if not parsed.path.endswith("/completion-evidence"):
            self._send(404, b'{"detail":"not found"}')
            return
        if self.headers.get_all("Accept-Encoding", failobj=[]) != ["identity"]:
            self._send(500, b'{"detail":"expected one identity encoding"}')
            return

        cursor = parse_qs(parsed.query).get("cursor", [""])[0]
        if cursor == "gzip-success":
            body = gzip.compress(
                json.dumps({"detail": SENSITIVE_MARKER}, separators=(",", ":")).encode()
            )
            self._send(200, body, content_encoding="gzip")
            return
        if cursor == "gzip-error":
            body = gzip.compress(
                json.dumps({"detail": SENSITIVE_MARKER}, separators=(",", ":")).encode()
            )
            self._send(503, body, content_encoding="gzip")
            return
        if cursor == "identity-limit":
            self._send(
                200,
                padded_json(HISTORY_MAX_BYTES),
                content_encoding="identity",
            )
            return
        if cursor == "identity-max-plus-one":
            self._send(
                200,
                padded_json(HISTORY_MAX_BYTES + 1),
                content_encoding="identity",
            )
            return

        page = {
            "work_item_id": "11111111-1111-4111-8111-111111111111",
            "work_version": 1,
            "lifecycle_status": "pending",
            "is_duplicate": False,
            "canonical_work_item_id": "11111111-1111-4111-8111-111111111111",
            "current_completion_checkpoint_id": None,
            "as_of_completion_event_id": None,
            "items": [],
            "total": 0,
            "structured_completion_total": 0,
            "limit": 10,
            "next_cursor": None,
        }
        self._send(
            200,
            json.dumps(page, separators=(",", ":")).encode(),
            content_encoding=None if cursor == "identity-absent" else "identity",
        )

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if not parsed.path.endswith("/complete"):
            self._send(404, b'{"detail":"not found"}')
            return
        declared = self.headers.get("Content-Length")
        if declared is None or not declared.isdecimal():
            self._send(411, b'{"detail":"content length required"}')
            return
        length = int(declared)
        body = self.rfile.read(length)
        if len(body) != length:
            self._send(400, b'{"detail":"incomplete request"}')
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, b'{"detail":"invalid JSON"}')
            return
        if not isinstance(payload, dict):
            self._send(400, b'{"detail":"object required"}')
            return
        response = json.dumps(
            {"accepted_body_bytes": len(body)},
            separators=(",", ":"),
        ).encode()
        self._send(200, response)

    def _send(
        self,
        status: int,
        body: bytes,
        *,
        content_encoding: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if content_encoding is not None:
            self.send_header("Content-Encoding", content_encoding)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
