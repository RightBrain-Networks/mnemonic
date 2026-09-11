"""Exercise the actual isolated Tika image with synthetic files, then clean it up.

Run on the Linux Docker host: uv run --project backend python scripts/test-artifact-tika.py
The parser has no published port, writable host mount, credentials or external
network. The host reaches only the disposable container's internal bridge address.
"""

import io
import json
import subprocess
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx
from mnemonic_api.artifact_tika import ExtractionError, TikaExtractor
from mnemonic_api.config import Settings
from mnemonic_api.transcript_parsers import TranscriptParserFactory

REPOSITORY = Path(__file__).resolve().parent.parent


def docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *arguments],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=check,
    )


def sample_docx() -> bytes:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1"
Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
Target="word/document.xml"/></Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>synthetic docx narwhal</w:t></w:r></w:p></w:body></w:document>""",
        )
    return content.getvalue()


def sample_pdf() -> bytes:
    stream = b"BT /F1 12 Tf 20 100 Td (synthetic pdf narwhal) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(result)
    result.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def wait_ready(origin: str) -> None:
    deadline = time.monotonic() + 45
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(origin + "/version")
                if response.status_code == 200 and "4.0.0" in response.text:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise AssertionError("Disposable Tika did not become ready")


def check_extraction(origin: str) -> None:
    extractor = TikaExtractor(
        Settings(
            database_url="postgresql://localhost/synthetic_test",
            api_key="test-only-" * 4,
            artifact_tika_url=origin,
            artifact_extraction_max_chars=1000,
        )
    )
    for name, content, token in [
        ("text.txt", b"synthetic plain narwhal", "plain narwhal"),
        ("document.docx", sample_docx(), "docx narwhal"),
        ("document.pdf", sample_pdf(), "pdf narwhal"),
        (
            "document.html",
            (
                b"<html><head><title>synthetic title</title>"
                b"<meta name='author' content='Synthetic Author'/></head><body>html narwhal</body></html>"
            ),
            "html narwhal",
        ),
    ]:
        result = extractor.extract(
            io.BytesIO(content), filename=name, size_bytes=len(content)
        )
        assert token in result.text, name
        assert result.metadata.get("Content-Type"), name
        if name.endswith("html"):
            assert result.metadata["dc:creator"] == ["Synthetic Author"]
    content = b"narwhal " * 1000
    result = extractor.extract(
        io.BytesIO(content), filename="large.txt", size_bytes=len(content)
    )
    assert result.truncated and 0 < len(result.text) <= 1000
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner.txt", "embedded narwhal")
    content = archive_bytes.getvalue()
    result = extractor.extract(
        io.BytesIO(content), filename="sample.zip", size_bytes=len(content)
    )
    assert "embedded narwhal" in result.text
    # XXE must not expose the parser's local files. No real secret is supplied.
    content = (
        b'<!DOCTYPE x [<!ENTITY probe SYSTEM "file:///etc/passwd">]><x>&probe;</x>'
    )
    try:
        result = extractor.extract(
            io.BytesIO(content), filename="probe.xml", size_bytes=len(content)
        )
        assert "root:" not in result.text
    except ExtractionError as exc:
        assert not exc.retryable
    with httpx.Client(trust_env=False, timeout=5) as client:
        assert client.post(origin + "/rmeta/config", content=b"{}").status_code in {
            403,
            415,
        }
        for path in ["/pipes", "/async", "/unpack", "/meta", "/detect"]:
            assert client.get(origin + path).status_code == 404, path
    print(
        "Actual Tika: text, HTML metadata, DOCX, PDF, embedded text, truncation and XXE passed"
    )


def check_transcripts(origin: str) -> None:
    extractor = TikaExtractor(Settings(
        database_url="postgresql://localhost/synthetic_test",
        api_key="test-only-" * 4, artifact_tika_url=origin,
        artifact_extraction_max_chars=1000,
    ))
    records = [
        {"type": "user", "sessionId": "synthetic", "message": {
            "role": "user", "content": "Find the transcript narwhal."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The transcript narwhal is indexed."},
            {"type": "tool_use", "name": "synthetic_tool", "input": {"needle": "narwhal"}},
        ]}},
    ]
    parser = TranscriptParserFactory.create("claude_code")
    for data in [
        "\n".join(json.dumps(row) for row in records).encode(),
        json.dumps(records).encode(), json.dumps({"messages": records}).encode(),
    ]:
        parsed = parser.parse(data, 1000)
        content = parsed.text.encode()
        result = extractor.extract(io.BytesIO(content), filename="transcript.txt",
                                   size_bytes=len(content))
        assert "transcript narwhal" in result.text
        assert "synthetic_tool" in result.text
        assert "user:" in result.text and "assistant:" in result.text
    print("Shared Tika: Claude Code JSONL, JSON array and message export extraction passed")


def check_isolation(name: str) -> None:
    info = json.loads(docker("inspect", name).stdout)[0]
    host = info["HostConfig"]
    assert info["Config"]["User"] == "35002:35002"
    assert host["ReadonlyRootfs"] and not info["Mounts"]
    assert host["PortBindings"] == {} and host["CapDrop"] == ["ALL"]
    assert host["Memory"] == host["MemorySwap"] == 2_147_483_648
    assert host["PidsLimit"] == 128 and host["NanoCpus"] == 2_000_000_000
    assert host["Dns"] == ["127.0.0.1"] and host["LogConfig"]["Type"] == "none"
    assert docker("exec", name, "bash", "/opt/mnemonic-tika/healthcheck.sh").returncode == 0
    network = next(iter(info["NetworkSettings"]["Networks"]))
    assert json.loads(docker("network", "inspect", network).stdout)[0]["Internal"]
    assert (
        docker(
            "exec", name, "sh", "-c", "test ! -e /var/lib/mnemonic/artifacts"
        ).returncode
        == 0
    )
    # The private network must not route outbound traffic even after parser compromise.
    connection = docker(
        "exec",
        name,
        "timeout",
        "2",
        "bash",
        "-c",
        "exec 3<>/dev/tcp/1.1.1.1/443",
        check=False,
    )
    assert connection.returncode != 0
    print(
        "Isolation: no external route, host content, public port, parser logs, or writable root"
    )


def main() -> None:
    name = "mnemonic-tika-smoke-" + uuid4().hex[:12]
    print(
        "Building and testing the pinned parser using synthetic files only", flush=True
    )
    docker("build", "-t", name, "./deploy/tika")
    docker("network", "create", "--internal", name)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            name,
            "--dns",
            "127.0.0.1",
            "--log-driver",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:size=1280m,mode=1777,noexec,nosuid,nodev",
            "--memory",
            "2g",
            "--memory-swap",
            "2g",
            "--cpus",
            "2",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--init",
            "-e",
            "MNEMONIC_ARTIFACT_EXTRACTION_MAX_CHARS=1000",
            name,
        )
        info = json.loads(docker("inspect", name).stdout)[0]
        ip = info["NetworkSettings"]["Networks"][name]["IPAddress"]
        origin = f"http://{ip}:9998"
        wait_ready(origin)
        check_extraction(origin)
        check_transcripts(origin)
        check_isolation(name)
    finally:
        docker("rm", "-f", name, check=False)
        docker("network", "rm", name, check=False)
        docker("image", "rm", name, check=False)
    print("Disposable parser and its temporary content removed")


if __name__ == "__main__":
    main()
