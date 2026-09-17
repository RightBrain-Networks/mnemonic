"""Reciprocal transcript references are bounded metadata, never implicit content."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_mcp.transcript_work_models import WorkTranscriptLinks


def test_transcript_links_reject_wrong_scope_duplicate_ids_and_missing_pages():
    project, work, identity = uuid4(), uuid4(), uuid4()
    row = {
        "id": identity,
        "project_id": project,
        "work_item_id": work,
        "filename": "session.jsonl",
        "client": "codex",
        "kind": "primary",
        "status": "ready",
        "last_updated_at": None,
        "session_ids": ["native"],
        "models": ["model"],
    }
    page = WorkTranscriptLinks(items=[row], total=1, omitted_count=0)
    page.require_scope(project, work)
    with pytest.raises(ValueError):
        page.require_scope(project, uuid4())
    for value in [
        {"items": [row, row], "total": 2, "omitted_count": 0},
        {"items": [row], "total": 2, "omitted_count": 1},
        {"items": [row | {"text": "unexpected body"}], "total": 1, "omitted_count": 0},
    ]:
        with pytest.raises(ValidationError):
            WorkTranscriptLinks.model_validate(value)
