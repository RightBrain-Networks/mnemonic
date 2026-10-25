"""Stream trusted knowledge attachments through the browser boundary."""

import re

from starlette.datastructures import Headers

from mnemonic_api.application.browser_file_transport import FileResponsePolicy, FileTransportError


def _headers(status: int, headers: Headers) -> dict[str, str] | None:
    if not 200 <= status < 300:
        return None
    disposition = headers.get("content-disposition", "")
    if status != 200 or not re.fullmatch(
        r'attachment; filename="[A-Z]{1,5}(?:-decisions\.tar\.gz|-[0-9]{5,19}\.(?:md|txt))"',
        disposition,
    ):
        raise FileTransportError("Invalid knowledge attachment response.")
    return {"Content-Type": "application/octet-stream", "Content-Disposition": disposition,
            "Content-Length": headers["content-length"]}


def knowledge_file_policy() -> FileResponsePolicy:
    return FileResponsePolicy(
        json_maximum=1024 * 1024,
        failure_detail="The knowledge download could not be completed.",
        redirect_detail="The knowledge download returned an unexpected redirect.",
        encoding_detail="The knowledge download returned an encoded response.",
        stream=_headers,
        # Archives are built on disk. Each document is bounded independently;
        # no arbitrary domain-wide export limit silently omits documents.
        stream_maximum=2**63 - 1,
    )
