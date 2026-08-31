import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.main import create_app
from mnemonic_api.schemas import (
    CheckpointCreate,
    CompletionCheckpointCreate,
    HandoffCommentCreate,
    HandoffCompletionCreate,
    HandoffCreate,
    HandoffPatch,
    LeaseTokenCreate,
    ProjectCreate,
    ProjectPatch,
    WorkClaimCreate,
    WorkCompletionCreate,
    WorkDeletionCreate,
    WorkItemCreate,
    WorkItemPatch,
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
    with pytest.raises(ValidationError):
        HandoffPatch(expected_version=1, status="done")


def test_legacy_patch_cannot_rewrite_checkpoint_fields():
    for field, value in [
        ("prompt", "rewritten"),
        ("source_client", "replacement"),
        ("source_session_id", "replacement"),
        ("repository_branch", None),
        ("verified_against", None),
        ("tags", ["replacement"]),
        ("source_metadata", {}),
    ]:
        with pytest.raises(ValidationError):
            HandoffPatch.model_validate({"expected_version": 1, field: value})


def test_canonical_create_priority_status_and_checkpoint_kind_contract(work_payload):
    assert WorkItemCreate.model_validate(work_payload).priority == 30
    for value in [-1, 101, True, 1.5]:
        with pytest.raises(ValidationError):
            WorkItemCreate.model_validate({**work_payload, "priority": value})
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload, "status": "done"})

    provenance = {
        "prompt": "Exact context.",
        "source_client": "claude-code",
        "source_session_id": "validation-session",
    }
    assert CheckpointCreate.model_validate(provenance).kind == "context"
    with pytest.raises(ValidationError):
        CheckpointCreate.model_validate({**provenance, "kind": "completion"})
    assert CompletionCheckpointCreate.model_validate(provenance).prompt == "Exact context."
    with pytest.raises(ValidationError):
        CompletionCheckpointCreate.model_validate({**provenance, "kind": "completion"})


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
    assert settings.lease_ttl_seconds == 900
    assert Settings(
        database_url="postgresql://localhost/mnemonic",
        api_key="x" * 32,
        lease_ttl_seconds=60,
    ).lease_ttl_seconds == 60
    for invalid_ttl in [59, 3601]:
        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql://localhost/mnemonic",
                api_key="x" * 32,
                lease_ttl_seconds=invalid_ttl,
            )


def test_lease_request_models_are_strict_and_bounded():
    claim = WorkClaimCreate(
        holder_client="claude-code",
        holder_session_id="opaque-session",
        claim_request_id="request-1",
    )
    assert claim.claim_request_id == "request-1"
    for payload in [
        {},
        {"holder_client": "client", "holder_session_id": "session"},
        {
            "holder_client": "client",
            "holder_session_id": "session",
            "claim_request_id": " ",
        },
        {
            "holder_client": "client",
            "holder_session_id": "session",
            "claim_request_id": "x" * 201,
        },
        {
            "holder_client": "client",
            "holder_session_id": "session",
            "claim_request_id": "request",
            "lease_token": "not-a-claim-field",
        },
    ]:
        with pytest.raises(ValidationError):
            WorkClaimCreate.model_validate(payload)
    with pytest.raises(ValidationError):
        LeaseTokenCreate.model_validate({"lease_token": " "})
    with pytest.raises(ValidationError):
        LeaseTokenCreate.model_validate({"lease_token": "x", "holder_client": "extra"})


def test_every_token_bearing_request_hides_capability_from_repr_but_serializes_it():
    lease_token = "raw-capability-token-that-must-not-appear-in-repr"
    checkpoint = {
        "prompt": "Exact checkpoint content.",
        "source_client": "claude-code",
        "source_session_id": "token-repr-session",
    }
    models = [
        CheckpointCreate(**checkpoint, lease_token=lease_token),
        WorkItemPatch(expected_version=1, title="Updated title", lease_token=lease_token),
        WorkCompletionCreate(
            expected_version=1,
            checkpoint=checkpoint,
            lease_token=lease_token,
        ),
        WorkDeletionCreate(expected_version=1, lease_token=lease_token),
        LeaseTokenCreate(lease_token=lease_token),
        HandoffPatch(expected_version=1, title="Updated title", lease_token=lease_token),
        HandoffCommentCreate(
            body="Exact legacy progress.",
            source_client="claude-code",
            source_session_id="token-repr-session",
            lease_token=lease_token,
        ),
        HandoffCompletionCreate(
            expected_version=1,
            summary="Exact legacy completion.",
            source_client="claude-code",
            source_session_id="token-repr-session",
            lease_token=lease_token,
        ),
    ]
    for model in models:
        assert lease_token not in repr(model), type(model).__name__
        assert model.model_dump()["lease_token"] == lease_token

    canonical_completion = models[2]
    assert lease_token not in repr(canonical_completion.checkpoint)
    assert canonical_completion.model_dump()["checkpoint"] == {
        **checkpoint,
        "source_model": None,
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": [],
        "source_metadata": {},
    }


def test_production_uvicorn_disables_access_logging():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "uvicorn mnemonic_api.main:create_app" in dockerfile
    assert "--no-access-log" in dockerfile


def test_application_error_context_uses_a_strict_safe_allowlist():
    error = ApplicationError(
        409,
        "lease_held",
        "This work item has an active lease.",
        context={
            "holder_client": "safe-client",
            "expires_at": "2026-08-31T18:15:00Z",
            "holder_session_id": "not-error-context",
            "lease_token": "never-expose-this",
            "prompt": "also-never-expose-this",
            "source_metadata": {"secret": True},
        },
    )
    assert error.detail["context"] == {
        "holder_client": "safe-client",
        "expires_at": "2026-08-31T18:15:00Z",
    }
    assert conflict(
        "lease_held",
        "Held.",
        context={"holder_client": "client", "lease_token": "hidden"},
    ).detail["context"] == {"holder_client": "client"}


def test_authentication_happens_without_database_and_health_is_public():
    settings = Settings(database_url="postgresql://localhost:1/unavailable", api_key="x" * 32)
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        for headers in [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}]:
            response = client.get("/api/v1/projects", headers=headers)
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == "Bearer"
            assert "x" * 32 not in response.text


def test_authentication_precedes_lease_query_validation():
    api_key = "x" * 32
    settings = Settings(database_url="postgresql://localhost:1/unavailable", api_key=api_key)
    project_id = "00000000-0000-0000-0000-000000000001"
    work_item_id = "00000000-0000-0000-0000-000000000002"
    lease_path = f"/api/v1/projects/{project_id}/work-items/{work_item_id}/claim"
    query_token = "unauthenticated-url-token-must-not-appear"

    with TestClient(create_app(settings)) as client:
        responses = [
            client.post(
                lease_path,
                params={"lease_token": query_token},
                json={"not": "a valid claim"},
            ),
            client.post(
                lease_path,
                params={"holder_client": "query-only"},
                json={"not": "a valid claim"},
            ),
            client.patch(
                f"/api/v1/projects/{project_id}/work-items/{work_item_id}",
                params={"lease_token": query_token},
                json={"not": "a valid patch"},
            ),
        ]

    for response in responses:
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"detail": "Valid bearer authentication is required"}
        assert query_token not in response.text


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
