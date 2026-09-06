import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mnemonic_api.application.validation import public_validation_errors
from mnemonic_api.config import Settings
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.main import create_app
from mnemonic_api.schemas import (
    CheckpointCreate,
    CheckpointRead,
    CompletionCheckpointCreate,
    InitialCheckpointCreate,
    LeaseTokenCreate,
    ProjectCreate,
    ProjectPatch,
    ProjectSettingsPatch,
    WorkClaimCreate,
    WorkCompletionCreate,
    WorkDeletionCreate,
    WorkEventRead,
    WorkItemCreate,
    WorkItemPatch,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCOPE_CORPUS = json.loads(
    (REPOSITORY_ROOT / "tests" / "fixtures" / "repository-freshness-scope-v1.json").read_text(
        encoding="utf-8"
    )
)


def generated_scope(case: dict[str, object]) -> list[str]:
    count = int(case["count"])
    entry_bytes = int(case["entry_bytes"])
    prefix_width = int(case["prefix_width"])
    fill = str(case["fill"])
    extra_bytes_on_first = int(case["extra_bytes_on_first"])
    paths = []
    for index in range(count):
        prefix = f"{index:0{prefix_width}d}"
        target_bytes = entry_bytes + (extra_bytes_on_first if index == 0 else 0)
        paths.append(prefix + fill * (target_bytes - len(prefix)))
    return paths


def checkpoint_read_payload(**changes):
    payload = {
        "id": str(uuid4()),
        "work_item_id": str(uuid4()),
        "kind": "context",
        "prompt": "Exact checkpoint context.",
        "source_client": "claude-code",
        "source_session_id": "phase-10-validation",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": [],
        "source_metadata": {},
        "migration_origin": None,
        "legacy_record_id": None,
        "created_at": datetime.now(UTC),
    }
    payload.update(changes)
    return payload


def test_prompt_is_exact_and_metadata_survives(checkpoint_fields):
    checkpoint_fields["prompt"] += "\nUnicode: café 日本語 🧠\t"
    checkpoint_fields["tags"] = ["  CACHE ", "cache", "Correctness"]
    parsed = InitialCheckpointCreate.model_validate(checkpoint_fields)
    assert parsed.prompt == checkpoint_fields["prompt"]
    assert parsed.source_metadata == checkpoint_fields["source_metadata"]
    assert parsed.tags == ["cache", "correctness"]
    assert parsed.source_session_id == "3d46fe7a-session:opaque_001"


@pytest.mark.parametrize(
    "affected_path",
    [
        "src/file.py",
        "src/**",
        "tests/test_*.py",
        "*",
        "**",
        "-leading-option-like-name",
        "A0._@+=,~-*",
    ],
)
def test_affected_path_valid_grammar_is_exact_and_preserved(affected_path):
    parsed = InitialCheckpointCreate.model_validate(
        {
            "prompt": "Scope validation.",
            "source_client": "pytest",
            "source_session_id": "phase-10",
            "verified_against": "ABCDEF1",
            "affected_paths": [affected_path],
        }
    )

    assert parsed.verified_against == "abcdef1"
    assert parsed.affected_paths == [affected_path]
    assert parsed.model_dump(mode="json")["affected_paths"] == [affected_path]


def test_affected_paths_empty_is_unknown_and_canonically_omitted_at_every_nesting():
    base = {
        "prompt": "No dependency scope was declared.",
        "source_client": "pytest",
        "source_session_id": "phase-10",
    }
    omitted = InitialCheckpointCreate.model_validate(base)
    explicit = InitialCheckpointCreate.model_validate({**base, "affected_paths": []})

    assert omitted.affected_paths == explicit.affected_paths == []
    assert omitted.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "affected_paths" not in explicit.model_dump(mode="json")

    work = WorkItemCreate.model_validate(
        {
            "title": "Sparse nested checkpoint",
            "summary": "Preserve the historical canonical request body.",
            "initial_checkpoint": {**base, "affected_paths": []},
        }
    )
    assert "affected_paths" not in work.model_dump(mode="json")["initial_checkpoint"]

    historical_read = CheckpointRead.model_validate(checkpoint_read_payload())
    explicit_empty_read = CheckpointRead.model_validate(checkpoint_read_payload(affected_paths=[]))
    assert historical_read.affected_paths == explicit_empty_read.affected_paths == []
    assert "affected_paths" not in explicit_empty_read.model_dump(mode="json")


def test_affected_paths_preserve_order_case_and_exact_boundary():
    paths = [f"{index:02d}" + "a" * 254 for index in range(64)]
    parsed = InitialCheckpointCreate.model_validate(
        {
            "prompt": "Maximum safe scope.",
            "source_client": "pytest",
            "source_session_id": "phase-10",
            "verified_against": "abcdef1",
            "affected_paths": paths,
        }
    )

    assert parsed.affected_paths == paths
    assert sum(len(path.encode("utf-8")) for path in parsed.affected_paths) == 16384
    assert InitialCheckpointCreate.model_validate(
        {
            "prompt": "Maximum entry.",
            "source_client": "pytest",
            "source_session_id": "phase-10-entry",
            "verified_against": "abcdef1",
            "affected_paths": ["A" * 512],
        }
    ).affected_paths == ["A" * 512]


@pytest.mark.parametrize(
    "affected_paths",
    [
        [""],
        ["/absolute"],
        ["trailing/"],
        ["repeated//separator"],
        ["./relative"],
        ["parent/../escape"],
        [r"windows\\path"],
        ["C:/drive"],
        ["has space"],
        ["caf\N{LATIN SMALL LETTER E WITH ACUTE}.py"],
        ["line\nbreak"],
        ["question?.py"],
        ["class[ab].py"],
        ["brace{a,b}.py"],
        ["quote'file"],
        ["dollar$file"],
        ["tick`file"],
        [":(glob)src/**"],
        ["!excluded"],
        ["^excluded"],
        ["bad**component"],
        ["***"],
        ["src/**suffix"],
        ["duplicate", "duplicate"],
        ["x" * 513],
        [f"path-{index}" for index in range(65)],
        ["a" * 257, *(f"{index:02d}" + "a" * 254 for index in range(1, 64))],
    ],
    ids=lambda value: repr(value)[:48],
)
def test_affected_paths_reject_every_unsupported_or_excess_form(affected_paths):
    with pytest.raises(ValidationError):
        InitialCheckpointCreate.model_validate(
            {
                "prompt": "Invalid dependency scope.",
                "source_client": "pytest",
                "source_session_id": "phase-10",
                "verified_against": "abcdef1",
                "affected_paths": affected_paths,
            }
        )


@pytest.mark.parametrize("affected_paths", [None, "src/**", [None], [1]])
def test_affected_paths_are_a_strict_string_list(affected_paths):
    with pytest.raises(ValidationError):
        InitialCheckpointCreate.model_validate(
            {
                "prompt": "Invalid dependency scope type.",
                "source_client": "pytest",
                "source_session_id": "phase-10",
                "verified_against": "abcdef1",
                "affected_paths": affected_paths,
            }
        )


def test_nonempty_affected_paths_require_a_baseline_on_inputs_and_reads():
    with pytest.raises(ValidationError):
        InitialCheckpointCreate.model_validate(
            {
                "prompt": "Missing baseline.",
                "source_client": "pytest",
                "source_session_id": "phase-10",
                "affected_paths": ["src/**"],
            }
        )
    with pytest.raises(ValidationError):
        CheckpointRead.model_validate(checkpoint_read_payload(affected_paths=["src/**"]))


def test_shared_repository_scope_corpus_matches_backend_validator():
    assert SCOPE_CORPUS["version"] == "repository-freshness-scope-v1"
    base = {
        "prompt": "Shared repository-scope corpus.",
        "source_client": "pytest",
        "source_session_id": "phase-10-shared-corpus",
        "verified_against": "abcdef1",
    }
    for path in SCOPE_CORPUS["valid_paths"]:
        parsed = InitialCheckpointCreate.model_validate({**base, "affected_paths": [path]})
        assert parsed.affected_paths == [path]
    for path in SCOPE_CORPUS["invalid_paths"]:
        with pytest.raises(ValidationError):
            InitialCheckpointCreate.model_validate({**base, "affected_paths": [path]})

    for case in SCOPE_CORPUS["generated_scopes"]:
        paths = generated_scope(case)
        assert sum(len(path.encode("ascii")) for path in paths) == int(case["expected_total_bytes"])
        if case["valid"]:
            assert (
                InitialCheckpointCreate.model_validate(
                    {**base, "affected_paths": paths}
                ).affected_paths
                == paths
            )
        else:
            with pytest.raises(ValidationError):
                InitialCheckpointCreate.model_validate({**base, "affected_paths": paths})
    for case in SCOPE_CORPUS["literal_scopes"]:
        with pytest.raises(ValidationError):
            InitialCheckpointCreate.model_validate({**base, "affected_paths": case["paths"]})

    supported = set(SCOPE_CORPUS["component_characters"])
    for codepoint in range(128):
        character = chr(codepoint)
        path = f"a{character}b"
        if character in supported or character == "/":
            assert InitialCheckpointCreate.model_validate(
                {**base, "affected_paths": [path]}
            ).affected_paths == [path]
        else:
            with pytest.raises(ValidationError):
                InitialCheckpointCreate.model_validate({**base, "affected_paths": [path]})

    with pytest.raises(ValidationError, match="verified_against"):
        InitialCheckpointCreate.model_validate(
            {
                **base,
                "verified_against": None,
                "affected_paths": [SCOPE_CORPUS["requires_baseline_path"]],
            }
        )


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
def test_rejects_invalid_capture(work_payload, checkpoint_fields, field, invalid):
    # Work identity fields belong to WorkItemCreate; every provenance field belongs to
    # the checkpoint model. Both reach the same shared Annotated types in schemas.py.
    if field in {"title", "summary", "status"}:
        with pytest.raises(ValidationError):
            WorkItemCreate.model_validate({**work_payload, field: invalid})
    else:
        with pytest.raises(ValidationError):
            InitialCheckpointCreate.model_validate({**checkpoint_fields, field: invalid})


def test_progress_and_completion_text_are_exact_and_require_provenance():
    body = "  Changed the parser.\r\n\nFocused tests passed. 🧠\n  "
    progress = CheckpointCreate(
        kind="progress",
        prompt=body,
        source_client="claude-code",
        source_session_id="real-session",
    )
    assert progress.kind == "progress"
    assert progress.prompt == body
    completion = CompletionCheckpointCreate(
        prompt=body,
        source_client="claude-code",
        source_session_id="real-session",
    )
    assert completion.prompt == body
    assert (
        WorkCompletionCreate.model_validate(
            {
                "expected_version": 2,
                "checkpoint": {
                    "prompt": body,
                    "source_client": "claude-code",
                    "source_session_id": "real-session",
                },
            }
        ).checkpoint.prompt
        == body
    )
    for model, payload in [
        (CheckpointCreate, {"kind": "progress", "prompt": body, "source_client": "claude-code"}),
        (CompletionCheckpointCreate, {"prompt": body, "source_client": "claude-code"}),
        (
            WorkCompletionCreate,
            {
                "expected_version": 2,
                "checkpoint": {"prompt": body, "source_client": "claude-code"},
            },
        ),
    ]:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_missing_provenance_is_not_invented(work_payload, checkpoint_fields):
    del checkpoint_fields["source_session_id"]
    with pytest.raises(ValidationError):
        InitialCheckpointCreate.model_validate(checkpoint_fields)
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload, "initial_checkpoint": checkpoint_fields})


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
        WorkItemPatch.model_validate(payload)


def test_done_requires_the_completion_workflow(work_payload):
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload, "status": "done"})
    with pytest.raises(ValidationError):
        WorkItemPatch(expected_version=1, status="done")


def test_patch_cannot_rewrite_checkpoint_fields():
    for field, value in [
        ("prompt", "rewritten"),
        ("source_client", "replacement"),
        ("source_session_id", "replacement"),
        ("repository_branch", None),
        ("verified_against", None),
        ("tags", ["replacement"]),
        ("source_metadata", {}),
        ("kind", "progress"),
    ]:
        with pytest.raises(ValidationError):
            WorkItemPatch.model_validate({"expected_version": 1, field: value})


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


def test_initial_relationships_are_bounded_and_discovery_is_outgoing_with_context(
    work_payload,
):
    discovery = {
        "type": "discovered-from",
        "direction": "outgoing",
        "other_work_item_id": str(uuid4()),
        "context_checkpoint_id": str(uuid4()),
    }
    parsed = WorkItemCreate.model_validate({**work_payload, "initial_relationships": [discovery]})
    assert parsed.initial_relationships[0].direction == "outgoing"

    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload, "initial_relationships": [discovery] * 11})
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate(
            {
                **work_payload,
                "initial_relationships": [{**discovery, "direction": "incoming"}],
            }
        )
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate(
            {
                **work_payload,
                "initial_relationships": [
                    {
                        key: value
                        for key, value in discovery.items()
                        if key != "context_checkpoint_id"
                    }
                ],
            }
        )


def test_project_slug_normalization():
    assert ProjectCreate(name="  Café / Tool Kit  ").slug == "cafe-tool-kit"
    assert ProjectCreate(name="日本語", slug="nihongo").slug == "nihongo"
    with pytest.raises(ValidationError):
        ProjectCreate(name="日本語")


@pytest.mark.parametrize(
    "payload",
    [{}, {"description": None}, {"name": None}, {"slug": None}, {"slug": "Not Valid"}],
)
def test_project_patch_rejects_invalid_edits(payload):
    with pytest.raises(ValidationError):
        ProjectPatch.model_validate(payload)


def test_project_patch_accepts_slug_edits():
    assert ProjectPatch.model_validate({"slug": "new-project"}).slug == "new-project"


def test_project_settings_patch_is_exact_nullable_and_bounded():
    template = "  Recall $WORK_ITEM_TITLE.\r\nKeep this spacing.\t "
    parsed = ProjectSettingsPatch(expected_revision="1", recall_pointer_template=template)
    assert parsed.recall_pointer_template == template
    assert (
        ProjectSettingsPatch(
            expected_revision="1", recall_pointer_template=None
        ).recall_pointer_template
        is None
    )

    for payload in [
        {},
        {"recall_pointer_template": " \r\n\t"},
        {"recall_pointer_template": "x" * 100001},
        {"recall_pointer_template": "NUL\x00byte"},
        {"recall_pointer_template": "Invalid Unicode \ud800"},
        {"recall_pointer_template": "valid", "unknown": "field"},
    ]:
        with pytest.raises(ValidationError):
            ProjectSettingsPatch.model_validate({"expected_revision": "1", **payload})


def test_project_settings_validation_location_is_public():
    assert public_validation_errors(
        [
            {
                "type": "value_error",
                "loc": ("body", "recall_pointer_template"),
                "msg": "caller-controlled message",
            }
        ]
    ) == [
        {
            "type": "value_error",
            "loc": ["body", "recall_pointer_template"],
            "msg": "Value is invalid.",
        }
    ]


def test_unknown_fields_rejected(work_payload, checkpoint_fields):
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload, "assignee": "someone"})
    with pytest.raises(ValidationError):
        InitialCheckpointCreate.model_validate({**checkpoint_fields, "assignee": "someone"})


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
    assert (
        Settings(
            database_url="postgresql://localhost/mnemonic",
            api_key="x" * 32,
            lease_ttl_seconds=60,
        ).lease_ttl_seconds
        == 60
    )
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
            "holder_session_id": "safe-session",
            "purpose": "code_review",
            "code_review_id": "21ef1a19-eb1a-4cd3-84f4-e379e0bf2cd4",
            "mode": "cold",
            "claim_request_id": "never-expose-request-id",
            "lease_token": "never-expose-this",
            "prompt": "also-never-expose-this",
            "source_metadata": {"secret": True},
            "fields": ["body", "caller-provided-secret"],
        },
    )
    assert error.detail["context"] == {
        "holder_client": "safe-client",
        "holder_session_id": "safe-session",
        "expires_at": "2026-08-31T18:15:00Z",
        "purpose": "code_review",
        "code_review_id": "21ef1a19-eb1a-4cd3-84f4-e379e0bf2cd4",
        "mode": "cold",
    }
    assert conflict(
        "lease_held",
        "Held.",
        context={"holder_client": "client", "lease_token": "hidden"},
    ).detail["context"] == {"holder_client": "client"}
    assert ApplicationError(
        422,
        "event_secret_echo",
        "Rejected.",
        context={"fields": ["metadata.value", "body", "body"]},
    ).detail["context"] == {"fields": ["body", "metadata.value"]}


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
    "body",
    [
        b"",
        b"{",
        b'{"client_operation_id":"not-a-uuid"}',
        b'{"payload":"' + b"x" * (2 * 1024 * 1024) + b'"}',
    ],
    ids=["empty", "malformed", "invalid-schema", "oversized"],
)
def test_unauthenticated_rest_bodies_are_rejected_before_parsing(body: bytes):
    settings = Settings(
        database_url="postgresql://localhost:1/unavailable",
        api_key="x" * 32,
    )
    path = "/api/v1/projects/00000000-0000-0000-0000-000000000001/work-items"

    with TestClient(create_app(settings)) as client:
        response = client.post(
            path,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Valid bearer authentication is required"}


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
def test_invalid_input_has_serializable_422_errors(changes, work_payload):
    settings = Settings(database_url="postgresql://localhost:1/unavailable", api_key="x" * 32)
    raw_body = json.dumps(
        {**work_payload, "initial_checkpoint": {**work_payload["initial_checkpoint"], **changes}},
        ensure_ascii=True,
        allow_nan=True,
    )
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/projects/00000000-0000-0000-0000-000000000000/work-items",
            content=raw_body,
            headers={"Authorization": "Bearer " + "x" * 32, "Content-Type": "application/json"},
        )
        assert response.status_code == 422, response.text
        assert isinstance(response.json()["detail"], list)
        assert all("input" not in error for error in response.json()["detail"])


def test_work_event_response_metadata_is_type_and_origin_specific():
    payload = {
        "id": 1,
        "project_id": uuid4(),
        "work_item_id": uuid4(),
        "event_type": "work_updated",
        "actor_kind": "unattributed",
        "actor_client": None,
        "actor_session_id": None,
        "actor_model": None,
        "body": None,
        "checkpoint_id": None,
        "lease_generation_id": None,
        "lease_release_id": None,
        "relationship_id": None,
        "relationship_source_work_item_id": None,
        "relationship_target_work_item_id": None,
        "relationship_context_checkpoint_work_item_id": None,
        "relationship_context_checkpoint_id": None,
        "relationship_direction": None,
        "counterpart_work_item_id": None,
        "metadata_version": 1,
        "metadata": {
            "changes": {"status": {"before": "open", "after": "open"}},
            "work_version": 2,
        },
        "origin": "live",
        "created_at": datetime.now(UTC),
    }

    parsed = WorkEventRead.model_validate(payload)
    assert parsed.model_dump(mode="json")["metadata"] == payload["metadata"]

    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(
            {
                **payload,
                "metadata": {
                    **payload["metadata"],
                    "unknown": "must fail closed",
                },
            }
        )
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(
            {
                **payload,
                "metadata": {
                    "changes": {"status": {"before": "open", "after": "done"}},
                    "work_version": 2,
                },
            }
        )
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(
            {
                **payload,
                "metadata": {
                    "changes": {
                        "title": {
                            "before": "Bounded title",
                            "after": "x" * 201,
                        }
                    },
                    "work_version": 2,
                },
            }
        )

    claimed = {
        **payload,
        "event_type": "work_claimed",
        "actor_kind": "client",
        "actor_client": "pytest",
        "actor_session_id": "typed-event",
        "lease_generation_id": uuid4(),
        "metadata": {"expires_at": "2026-09-01T12:00:00Z"},
    }
    WorkEventRead.model_validate(claimed)
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(
            {
                **claimed,
                "metadata": {"expires_at": "2026-09-01T12:00:00"},
            }
        )

    deleted = {
        **payload,
        "event_type": "work_deleted",
        "origin": "backfill",
        "metadata": {"final_status": "wont-do", "final_version": 4},
    }
    WorkEventRead.model_validate(deleted)
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(
            {
                **deleted,
                "actor_kind": "client",
                "actor_client": "legacy-client",
                "actor_session_id": "legacy-session",
            }
        )

    source_id, target_id = sorted((uuid4(), uuid4()))
    relationship = {
        **payload,
        "work_item_id": source_id,
        "event_type": "relationship_added",
        "actor_kind": "client",
        "actor_client": "pytest",
        "actor_session_id": "relationship-response",
        "relationship_id": uuid4(),
        "relationship_source_work_item_id": source_id,
        "relationship_target_work_item_id": target_id,
        "relationship_context_checkpoint_work_item_id": target_id,
        "relationship_context_checkpoint_id": uuid4(),
        "relationship_direction": "outgoing",
        "counterpart_work_item_id": target_id,
        "metadata": {"relationship_type": "discovered-from"},
    }
    WorkEventRead.model_validate(relationship)

    incoming_relationship = {
        **relationship,
        "work_item_id": target_id,
        "relationship_direction": "incoming",
        "counterpart_work_item_id": source_id,
    }
    WorkEventRead.model_validate(incoming_relationship)

    invalid_relationships = [
        {"work_item_id": uuid4()},
        {"counterpart_work_item_id": source_id},
        {"relationship_direction": "incoming"},
        {
            "relationship_context_checkpoint_work_item_id": uuid4(),
        },
        {
            "relationship_context_checkpoint_work_item_id": None,
            "relationship_context_checkpoint_id": None,
        },
        {"relationship_context_checkpoint_work_item_id": source_id},
    ]
    for invalid_projection in invalid_relationships:
        with pytest.raises(ValidationError):
            WorkEventRead.model_validate({**relationship, **invalid_projection})

    valid_related = {
        **relationship,
        "metadata": {"relationship_type": "related"},
        "relationship_context_checkpoint_work_item_id": None,
        "relationship_context_checkpoint_id": None,
        "relationship_direction": "undirected",
    }
    WorkEventRead.model_validate(valid_related)

    reversed_related = {
        **valid_related,
        "work_item_id": target_id,
        "relationship_source_work_item_id": target_id,
        "relationship_target_work_item_id": source_id,
        "counterpart_work_item_id": source_id,
    }
    with pytest.raises(ValidationError):
        WorkEventRead.model_validate(reversed_related)


@pytest.mark.parametrize(
    ("event_type", "metadata", "requires_client", "has_checkpoint"),
    [
        (
            "work_created",
            {
                "initial": {
                    "title": "Historical open work",
                    "summary": "A Phase 5 event remains readable after Phase 7.",
                    "status": "open",
                    "priority": 10,
                    "version": 1,
                }
            },
            True,
            True,
        ),
        (
            "work_updated",
            {
                "changes": {"status": {"before": "open", "after": "open"}},
                "work_version": 2,
            },
            False,
            False,
        ),
        (
            "work_status_changed",
            {
                "from_status": "open",
                "to_status": "wont-do",
                "changes": {"status": {"before": "open", "after": "wont-do"}},
                "work_version": 2,
            },
            False,
            False,
        ),
        (
            "work_reopened",
            {
                "from_status": "done",
                "to_status": "open",
                "changes": {"status": {"before": "done", "after": "open"}},
                "work_version": 3,
            },
            False,
            False,
        ),
        (
            "work_completed",
            {"from_status": "open", "to_status": "done", "work_version": 2},
            True,
            True,
        ),
        (
            "work_deleted",
            {"final_status": "open", "final_version": 2},
            False,
            False,
        ),
    ],
)
def test_phase7_preserves_historical_open_event_metadata(
    event_type, metadata, requires_client, has_checkpoint
):
    payload = {
        "id": 1,
        "project_id": uuid4(),
        "work_item_id": uuid4(),
        "event_type": event_type,
        "actor_kind": "client" if requires_client else "unattributed",
        "actor_client": "legacy-client" if requires_client else None,
        "actor_session_id": "legacy-session" if requires_client else None,
        "actor_model": None,
        "body": None,
        "checkpoint_id": uuid4() if has_checkpoint else None,
        "lease_generation_id": None,
        "lease_release_id": None,
        "relationship_id": None,
        "relationship_source_work_item_id": None,
        "relationship_target_work_item_id": None,
        "relationship_context_checkpoint_work_item_id": None,
        "relationship_context_checkpoint_id": None,
        "relationship_direction": None,
        "counterpart_work_item_id": None,
        "metadata_version": 1,
        "metadata": metadata,
        "origin": "live",
        "created_at": datetime.now(UTC),
    }

    parsed = WorkEventRead.model_validate(payload)

    serialized_metadata = parsed.model_dump(mode="json")["metadata"]
    assert serialized_metadata == metadata
    assert "open" in json.dumps(serialized_metadata)


def test_validation_error_sanitizer_allowlists_locations_and_drops_raw_content():
    root_key = "SENSITIVE_CALLER_KEY_123"
    root_value = "root-private-content-123"
    nested_key = "NESTED_PRIVATE_KEY_456"
    nested_value = "nested-private-content-456"
    errors = public_validation_errors(
        [
            {
                "type": "extra_forbidden",
                "loc": ("body", root_key),
                "msg": root_value,
                "input": root_value,
                "ctx": {"error": nested_value},
            },
            {
                "type": "extra_forbidden",
                "loc": ("body", "actor", nested_key),
                "msg": nested_value,
                "input": {nested_key: nested_value},
            },
            {
                "type": "missing",
                "loc": ("body", "actor", "actor_client"),
                "msg": root_value,
            },
            {
                "type": "value_error",
                "loc": ("body", "affected_paths", 0),
                "msg": nested_value,
                "input": nested_value,
                "ctx": {"error": nested_value},
            },
            {
                "type": root_value,
                "loc": ("body", "metadata", nested_key),
                "msg": nested_value,
                "input": nested_value,
            },
        ]
    )
    assert errors == [
        {
            "type": "extra_forbidden",
            "loc": ["body", "field"],
            "msg": "Extra inputs are not permitted.",
        },
        {
            "type": "extra_forbidden",
            "loc": ["body", "actor", "field"],
            "msg": "Extra inputs are not permitted.",
        },
        {
            "type": "missing",
            "loc": ["body", "actor", "actor_client"],
            "msg": "Field required.",
        },
        {
            "type": "value_error",
            "loc": ["body", "affected_paths", 0],
            "msg": "Value is invalid.",
        },
        {
            "type": "validation_error",
            "loc": ["body", "metadata", "field"],
            "msg": "Request validation failed.",
        },
    ]
    serialized = json.dumps(errors)
    for private_content in (root_key, root_value, nested_key, nested_value):
        assert private_content not in serialized


@pytest.mark.parametrize(
    "segment",
    (
        "completion_evidence",
        "verification_results",
        "artifact_references",
        "verification_type",
        "outcome",
        "command",
        "exit_code",
        "observed_at",
        "observed_at_commit",
        "artifact_type",
        "label",
        "reference",
    ),
)
def test_completion_evidence_validation_locations_are_public(segment: str):
    assert public_validation_errors(
        [
            {
                "type": "value_error",
                "loc": ("body", segment),
                "msg": "private model error",
            }
        ]
    ) == [
        {
            "type": "value_error",
            "loc": ["body", segment],
            "msg": "Value is invalid.",
        }
    ]
