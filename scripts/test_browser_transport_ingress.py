"""Wire-level probes for the isolated nginx/FastAPI transport acceptance stack."""

import base64
import hashlib
import http.client
import json
import os
import re
import socket
import struct
import time
from urllib.parse import urlsplit
from uuid import uuid4

ORIGIN = os.environ["MNEMONIC_NGINX_E2E_URL"]
PROJECT = "11111111-1111-4111-8111-111111111111"
RAW_PREFIX = b"raw-browser-stream\x00\xff\n"
RAW_SUFFIX = b"stream-complete\x00\xfe\n"


def connection(base=ORIGIN):
    url = urlsplit(base)
    return http.client.HTTPConnection(url.hostname, url.port, timeout=15)


def request(path, *, method="GET", body=None, headers=None, base=ORIGIN, chunked=False):
    client = connection(base)
    client.request(method, path, body=body, headers=headers or {}, encode_chunked=chunked)
    response = client.getresponse()
    status, response_headers, content = (
        response.status,
        dict(response.getheaders()),
        response.read(),
    )
    client.close()
    return status, {key.lower(): value for key, value in response_headers.items()}, content


def secure(headers):
    for key, expected in {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "cross-origin-resource-policy": "same-origin",
        "x-dns-prefetch-control": "off",
        "strict-transport-security": "max-age=86400",
        "permissions-policy": "camera=(), microphone=(), geolocation=()",
    }.items():
        assert headers.get(key) == expected, (key, headers.get(key))
    assert "no-store" in headers["cache-control"] and "no-transform" in headers["cache-control"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def public_payload(content):
    for name in ("MNEMONIC_E2E_API_KEY", "MNEMONIC_E2E_BACKUP_TOKEN"):
        assert os.environ[name].encode() not in content, "Private credential in public bytes"
    for address in (b"http://api:8000", b"http://worker:8002"):
        assert address not in content, "Private service address in public bytes"


def runtime_configuration():
    status, headers, content = request("/api/mnemonic/runtime-config")
    assert status == 200
    secure(headers)
    public_payload(content)
    assert json.loads(content) == {
        "timezone": os.environ.get("TIMEZONE", "America/Detroit"),
        "work_summary_max_chars": int(
            os.environ.get("MNEMONIC_E2E_WORK_SUMMARY_MAX_CHARS", "2048")
        ),
        "artifact_max_bytes": 67108864,
        "backup_max_bytes": 2097152,
    }
    print("PASS: runtime configuration matches deployment values and exposes only public fields")


def static_delivery():
    status, headers, page = request("/")
    assert status == 200 and "text/html" in headers["content-type"]
    public_payload(page)
    assert "no-cache" in headers["cache-control"]
    assert headers["x-frame-options"] == "DENY"
    for path in (
        "/work-items?project=example&selected=example",
        "/attention", "/code-reviews", "/summaries", "/artifacts", "/transcripts",
        "/settings/workspace", "/settings/prompts", "/settings/code-reviews",
        "/settings/backups",
    ):
        status, route_headers, content = request(path)
        assert status == 200 and content == page, path
        assert "no-cache" in route_headers["cache-control"], path
    assets = re.findall(rb'(?:src|href)="(/assets/[^\"]+)"', page)
    assert assets, "The built SPA did not reference fingerprinted assets"
    for asset in assets:
        path = asset.decode()
        status, asset_headers, content = request(path)
        assert status == 200 and content != page, path
        public_payload(content)
        assert "immutable" in asset_headers["cache-control"], path
        assert asset_headers["x-content-type-options"] == "nosniff", path
        assert "text/html" not in asset_headers["content-type"], path
    for path in (
        "/assets/missing.js", "/missing.css", "/api/unknown", "/readyz",
        "/.env", "/api/mnemonic/unknown", "/api/artifacts/unknown",
        "/api/transcripts/unknown", "/api/backups/unknown",
        "/settings", "/missing-route", "/settings/missing",
    ):
        status, _, content = request(path)
        assert status == 404 and content != page, (path, status)
    status, _, content = request("/healthz")
    assert status == 200 and json.loads(content) == {"status": "ok"}
    print(
        "PASS: static SPA deep links, secret-free assets, immutable caching, "
        "security headers and missing-resource boundaries"
    )


def raw_streams():
    for family, resource in (("artifacts", "artifacts"), ("transcripts", "transcripts")):
        path = f"/api/{family}/projects/{PROJECT}/{resource}/{PROJECT}/content"
        client = connection()
        started = time.monotonic()
        client.request("GET", path, headers={"Accept-Encoding": "gzip, br"})
        response = client.getresponse()
        assert response.status == 200
        secure({key.lower(): value for key, value in response.getheaders()})
        policies = [value for name, value in response.getheaders()
                    if name.lower() == "content-security-policy"]
        assert any("sandbox" in policy and "default-src 'none'" in policy
                   for policy in policies), policies
        # The delayed second domain chunk proves nginx and ASGI do not collect
        # the whole response before exposing the first binary bytes.
        assert response.read(len(RAW_PREFIX)) == RAW_PREFIX
        assert time.monotonic() - started < 1.5, "The raw response was buffered"
        assert response.read() == RAW_SUFFIX
        if family == "transcripts":
            assert response.getheader("Transfer-Encoding") == "chunked"
        else:
            assert int(response.getheader("Content-Length")) == len(RAW_PREFIX + RAW_SUFFIX)
        client.close()

    body = bytes(range(256)) * 8192
    status, headers, result = request(
        f"/api/artifacts/projects/{PROJECT}/artifacts",
        method="POST",
        body=(body[offset : offset + 65536] for offset in range(0, len(body), 65536)),
        chunked=True,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/octet-stream",
            "X-Artifact-Metadata": '{"filename":"raw.bin"}',
            "X-Client-Operation-ID": str(uuid4()),
        },
    )
    assert status == 200, (status, result)
    secure(headers)
    assert json.loads(result) == {
        "body_bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    print(
        "PASS: artifact/transcript raw streams flush before completion; "
        "chunked artifact bytes are exact"
    )


def unbuffered_upload():
    path = f"/api/artifacts/projects/{PROJECT}/artifacts"
    chunk = b"request-buffer-probe\x00\xff" * 4096
    client = connection()
    client.putrequest("POST", path)
    for key, value in {
        "Origin": ORIGIN,
        "Content-Type": "application/octet-stream",
        "X-Artifact-Metadata": '{"filename":"streaming-probe.bin"}',
        "X-Client-Operation-ID": str(uuid4()),
        "Transfer-Encoding": "chunked",
    }.items():
        client.putheader(key, value)
    client.endheaders()
    client.send(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
    # The upload is deliberately unfinished. A second request must observe the
    # first bytes in the domain handler before nginx has the complete body.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status, _, body = request(path + "/" + PROJECT)
        assert status == 200, (status, body)
        if json.loads(body)["body_bytes"] == len(chunk):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("The edge buffered the unfinished raw upload")
    client.send(b"0\r\n\r\n")
    response = client.getresponse()
    assert response.status == 200
    assert json.loads(response.read()) == {
        "body_bytes": len(chunk),
        "sha256": hashlib.sha256(chunk).hexdigest(),
    }
    client.close()
    print("PASS: raw upload bytes reach the domain before the client finishes its chunked request")


def receive_exact(sock, count):
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        assert chunk, "WebSocket ended early"
        data += chunk
    return data


def websocket():
    url = urlsplit(ORIGIN)
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection((url.hostname, url.port), timeout=10) as sock:
        sock.sendall(
            (
                f"GET /api/mnemonic/sync HTTP/1.1\r\nHost: {url.netloc}\r\n"
                f"Origin: {ORIGIN}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += receive_exact(sock, 1)
        assert head.startswith(b"HTTP/1.1 101 "), head
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        assert b"sec-websocket-accept: " + expected.lower() in head.lower()
        opcode, length = receive_exact(sock, 2)
        assert opcode == 0x81 and length < 126
        message = json.loads(receive_exact(sock, length))
        assert message.keys() == {"type", "revision"} and message["type"] == "ready"
        mask = os.urandom(4)
        payload = struct.pack("!H", 1000)
        sock.sendall(b"\x88\x82" + mask + bytes(value ^ mask[i] for i, value in enumerate(payload)))
    print("PASS: nginx upgrades the production FastAPI WebSocket and receives its ready frame")


def boundaries():
    status, headers, content = request("/api/mnemonic/runtime-config")
    assert status == 200
    secure(headers)
    assert json.loads(content).keys() == {
        "timezone",
        "work_summary_max_chars",
        "artifact_max_bytes",
        "backup_max_bytes",
    }
    for path in (
        "/api/v1/projects",
        "/api/v1/sync",
        "/worker/healthz",
        "/projects/x/backups",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    ):
        status, _, content = request(
            path, headers={"Authorization": "Bearer " + os.environ["MNEMONIC_E2E_API_KEY"]}
        )
        assert status == 404, (path, status, content)
    for family in ("mnemonic", "artifacts", "transcripts", "backups"):
        status, headers, _ = request(
            f"/api/{family}/unknown", headers={"Origin": "https://evil.example"}
        )
        assert status == 403, (family, status)
        secure(headers)
    path = f"/api/mnemonic/projects/{PROJECT}/work-items/{PROJECT}/completion-evidence"
    status, _, content = request(path)
    assert status == 200 and content == b'{"completion_event_id":9007199254740993}'
    print(
        "PASS: four families enforce origin/security policy; "
        "private and docs routes stay closed; integer bytes survive"
    )


def backup_worker():
    status, _, content = request(
        "/api/v1/projects",
        method="POST",
        base=os.environ["MNEMONIC_E2E_API_URL"],
        body=json.dumps({"name": "Nginx transport probe", "slug": "nginx-" + str(uuid4())}),
        headers={
            "Authorization": "Bearer " + os.environ["MNEMONIC_E2E_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    assert status == 201, (status, content)
    project = json.loads(content)["id"]
    route = f"/api/backups/projects/{project}"
    status, headers, content = request(route + "/backups")
    assert status == 200 and json.loads(content)["project_id"] == project
    secure(headers)
    status, _, content = request(route + "/backups", method="POST", headers={"Origin": ORIGIN})
    assert status == 202, (status, content)
    job = json.loads(content)["job_id"]
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status, _, content = request(route + "/backup-jobs/" + job)
        assert status == 200, (status, content)
        state = json.loads(content)["state"]
        assert state != "failed", content
        if state == "succeeded":
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Backup job did not complete")
    status, _, content = request(route + "/backups")
    archive = json.loads(content)["backups"][0]
    status, headers, raw = request(route + "/backups/" + archive["filename"])
    assert status == 200 and raw.startswith(b"BZh")
    assert int(headers["content-length"]) == len(raw)
    assert headers["content-type"] == "application/octet-stream"
    secure(headers)
    status, headers, content = request(
        route + "/restore",
        method="POST",
        body=iter([raw[:3], raw[3:]]),
        chunked=True,
        headers={
            "Origin": ORIGIN,
            "Content-Type": "application/octet-stream",
            "X-Confirm-Project": project,
        },
    )
    assert status == 200 and json.loads(content) == {"project_id": project, "restored": True}, (
        status,
        content,
    )
    secure(headers)
    upload_headers = {
        "Origin": ORIGIN,
        "Content-Type": "application/octet-stream",
        "X-Confirm-Project": project,
    }
    maximum = 2 * 1024 * 1024  # The isolated Compose backup upload setting.
    status, _, _ = request(
        route + "/restore", method="POST", body=b"",
        headers={**upload_headers, "Content-Length": str(maximum + 1)},
    )
    assert status == 413, status
    oversized = raw + b" " * (maximum + 1 - len(raw))
    status, _, _ = request(
        route + "/restore", method="POST",
        body=(oversized[offset:offset + 65536] for offset in range(0, len(oversized), 65536)),
        chunked=True, headers=upload_headers,
    )
    assert status == 413, status
    print(
        "PASS: real worker backup create/poll/download "
        "and chunked raw restore traverse FastAPI/nginx; declared/chunked limits hold"
    )


if __name__ == "__main__":
    assert os.environ["MNEMONIC_E2E_COMPOSE_PROJECT"].startswith("mnemonic-nginx-e2e-")
    static_delivery()
    runtime_configuration()
    boundaries()
    raw_streams()
    unbuffered_upload()
    websocket()
    backup_worker()
