import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app
from mnemonic_api.schemas import (
    HandoffCommentCreate,
    HandoffCompletionCreate,
    HandoffCreate,
    HandoffPatch,
    ProjectCreate,
    ProjectPatch,
)


def test_prompt_is_exact_and_metadata_survives(handoff_payload):
    handoff_payload["prompt"] += "\nUnicode: café 日本語 🧠\t"
    handoff_payload["tags"] = ["  CACHE ", "cache", "Correctness"]
    parsed = HandoffCreate.model_validate(handoff_payload)
    assert parsed.prompt == handoff_payload["prompt"]
    assert parsed.source_metadata == handoff_payload["source_metadata"]
    assert parsed.tags == ["cache", "correctness"]
    assert parsed.source_session_id == "3d46fe7a-session:opaque_001"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("title", " \n\t"),
        ("title", "x" * 201),
        ("summary", ""),
        ("summary", "x" * 1001),
        ("prompt", "\r\n\t "),
        ("prompt", "x" * 100001),
        ("prompt", "NUL\x00byte"),
        ("prompt", "Invalid Unicode \ud800"),
        ("source_client", " "),
        ("source_session_id", 12345),
        ("source_session_id", "x" * 201),
        ("source_session_id", ""),
        ("source_model", "x" * 121),
        ("source_session_url", "file:///etc/passwd"),
        ("source_session_url", "https://user:password@example.com/session"),
        ("source_session_url", "https://example.com:notaport/"),
        ("repository_branch", "x" * 201),
        ("verified_against", "main"),
        ("verified_against", "z" * 40),
        ("tags", ["x"] * 21),
        ("tags", [" "]),
        ("tags", ["x" * 51]),
        ("tags", ["\u0130" * 50]),
        ("source_metadata", ["not", "an", "object"]),
        ("source_metadata", {"oversize": "x" * 16384}),
        ("source_metadata", {"nul": "\x00"}),
        ("source_metadata", {"not_json": float("nan")}),
        ("status", "archived"),
    ],
    ids=lambda value: repr(value)[:32],
)
def test_rejects_invalid_capture(handoff_payload, field, invalid):
    handoff_payload[field] = invalid
    with pytest.raises(ValidationError):
        HandoffCreate.model_validate(handoff_payload)


def test_comment_and_completion_text_are_exact_and_require_provenance():
    body = "  Changed the parser.\r\n\nFocused tests passed. 🧠\n  "
    comment = HandoffCommentCreate(
        body=body,
        source_client="claude-code",
        source_session_id="real-session",
    )
    assert comment.body == body
    completion = HandoffCompletionCreate(
        expected_version=2,
        summary=body,
        source_client="claude-code",
        source_session_id="real-session",
    )
    assert completion.summary == body
    for model, payload in [
        (HandoffCommentCreate, {"body": body, "source_client": "claude-code"}),
        (
            HandoffCompletionCreate,
            {
                "expected_version": 2,
                "summary": body,
                "source_client": "claude-code",
            },
        ),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.parametrize("body", [" ", "x" * 50001, "NUL\x00comment", "Invalid\ud800"])
def test_comment_body_validation(body):
    with pytest.raises(ValidationError):
        HandoffCommentCreate(
            body=body,
            source_client="dashboard",
            source_session_id="browser-session",
        )


def test_missing_provenance_is_not_invented(handoff_payload):
    del handoff_payload["source_session_id"]
    with pytest.raises(ValidationError):
        HandoffCreate.model_validate(handoff_payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "Missing version"},
        {"expected_version": 1},
        {"expected_version": True, "title": "Boolean is not a version"},
        {"expected_version": 0, "title": "Invalid"},
        {"expected_version": 1, "prompt": None},
        {"expected_version": 1, "tags": None},
        {"expected_version": 1, "status": None},
        {"expected_version": 1, "source_metadata": None},
        {"expected_version": 1, "source_session_id": "replacement"},
        {"expected_version": 1, "source_client": "different-client"},
        {"expected_version": 1, "source_model": "different-model"},
        {"expected_version": 1, "source_session_url": None},
        {"expected_version": 1, "project_id": "different-project"},
    ],
)
def test_patch_requires_version_and_preserves_immutable_provenance(payload):
    with pytest.raises(ValidationError):
        HandoffPatch.model_validate(payload)


def test_done_requires_the_completion_workflow(handoff_payload):
    handoff_payload["status"] = "done"
    with pytest.raises(ValidationError):
        HandoffCreate.model_validate(handoff_payload)
    with pytest.raises(ValidationError, match="completion endpoint"):
        HandoffPatch(expected_version=1, status="done")


def test_patch_can_explicitly_clear_optional_editable_fields():
    patch = HandoffPatch(expected_version=1, repository_branch=None, verified_against=None)
    assert patch.model_dump(exclude_unset=True) == {
        "expected_version": 1,
        "repository_branch": None,
        "verified_against": None,
    }


def test_project_slug_normalization():
    assert ProjectCreate(name="  Café / Tool Kit  ").slug == "cafe-tool-kit"
    assert ProjectCreate(name="日本語", slug="nihongo").slug == "nihongo"
    with pytest.raises(ValidationError):
        ProjectCreate(name="日本語")


@pytest.mark.parametrize("payload", [{}, {"description": None}, {"name": None}, {"slug": "new"}])
def test_project_patch_rejects_invalid_edits(payload):
    with pytest.raises(ValidationError):
        ProjectPatch.model_validate(payload)


def test_unknown_fields_rejected(handoff_payload):
    with pytest.raises(ValidationError):
        HandoffCreate.model_validate({**handoff_payload, "assignee": "someone"})


def test_settings_require_long_key_and_postgres():
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/mnemonic", api_key="short")
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///db.sqlite", api_key="x" * 32)
    settings = Settings(database_url="postgresql://user:secret@localhost/db", api_key="x" * 32)
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert "secret" not in repr(settings)
    assert "x" * 32 not in repr(settings)


def test_authentication_happens_without_database_and_health_is_public():
    settings = Settings(database_url="postgresql://localhost:1/unavailable", api_key="x" * 32)
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        for headers in [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}]:
            response = client.get("/api/v1/projects", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert "x" * 32 not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"source_metadata": {"invalid": float("nan")}},
        {"source_metadata": {"invalid": float("inf")}},
        {"source_metadata": {"\ud800": {"invalid": float("nan")}}},
        {"prompt": "NUL\x00prompt"},
        {"prompt": "Invalid\ud800Unicode"},
    ],
)
def test_invalid_input_has_serializable_422_errors(changes, handoff_payload):
    settings = Settings(database_url="postgresql://localhost:1/unavailable", api_key="x" * 32)
    raw_body = json.dumps({**handoff_payload, **changes}, ensure_ascii=True, allow_nan=True)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/handoffs",
            content=raw_body,
            headers={"Authorization": "Bearer " + "x" * 32, "Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert isinstance(response.json()["detail"], list)
        assert all("input" not in error for error in response.json()["detail"])
