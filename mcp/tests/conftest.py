import pytest

from mnemonic_mcp.config import Settings

API_KEY = "mnemonic-test-secret-" + "x" * 32
PROJECT_ID = "ca501b3f-860b-4f88-bca8-2f22a06359ab"
WORK_ID = "9b0e1e53-2a04-443d-a37c-d0a19cf760a8"
CHECKPOINT_ID = "74ce5a36-7295-45e7-bc24-5aa13ed4f293"
NOW = "2026-08-30T12:00:00Z"
EXPIRES_AT = "2026-08-30T12:15:00Z"
CLAIM_REQUEST_ID = "claim-request-phase-2-001"
LEASE_TOKEN = "lease-" + "t" * 43
OTHER_WORK_ID = "17956493-a5bc-49ae-a099-ead952f2dec8"
OTHER_CHECKPOINT_ID = "0663bc2f-42de-487a-b1d0-d3f8dbffbc0c"
RELATIONSHIP_ID = "6ba44356-d44d-4515-b502-653642fe723f"

PRIVATE_PROMPT_MARKER = "private-prompt-marker"
PRIVATE_METADATA_MARKER = "private-metadata-marker"
PRIVATE_UUID_MARKER = "private-uuid-marker"
PRIVATE_CLAIM_REQUEST_MARKER = "private-claim-request-marker"
PRIVATE_LEASE_TOKEN_MARKER = "private-lease-token-marker"
PRIVATE_EXTRA_FIELD = "private_extra_argument"
PRIVATE_EXTRA_VALUE = "private-extra-value-marker"

LOCAL_VALIDATION_CASES = (
    (
        "create_work",
        {
            "project_id": PROJECT_ID,
            "title": "Validation boundary",
            "summary": "Reject invalid checkpoint content locally.",
            "initial_checkpoint": {
                "prompt": (
                    PRIVATE_PROMPT_MARKER
                    + "p" * 100_001
                    + PRIVATE_PROMPT_MARKER
                ),
                "source_client": "test-client",
                "source_session_id": "test-session",
                "source_metadata": [PRIVATE_METADATA_MARKER],
            },
        },
        (
            "initial_checkpoint.prompt (string_too_long)",
            "initial_checkpoint.source_metadata (dict_type)",
        ),
        (PRIVATE_PROMPT_MARKER, PRIVATE_METADATA_MARKER),
        (),
    ),
    (
        "claim_work",
        {
            "project_id": PRIVATE_UUID_MARKER,
            "work_item_id": WORK_ID,
            "holder_client": "test-client",
            "holder_session_id": "test-session",
            "claim_request_id": PRIVATE_CLAIM_REQUEST_MARKER + "c" * 201,
        },
        ("claim_request_id (string_too_long)", "project_id (uuid_parsing)"),
        (PRIVATE_UUID_MARKER, PRIVATE_CLAIM_REQUEST_MARKER),
        (),
    ),
    (
        "renew_claim",
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "lease_token": PRIVATE_LEASE_TOKEN_MARKER + "t" * 201,
        },
        ("lease_token (value_error)",),
        (PRIVATE_LEASE_TOKEN_MARKER,),
        (),
    ),
    (
        # extra_forbidden names the caller's own unknown key, so only the kind
        # is reported and the key itself never appears.
        "list_projects",
        {PRIVATE_EXTRA_FIELD: PRIVATE_EXTRA_VALUE},
        (),
        (PRIVATE_EXTRA_FIELD, PRIVATE_EXTRA_VALUE),
        ("extra_forbidden",),
    ),
)


def expected_validation_message(fields: tuple[str, ...], kinds: tuple[str, ...] = ()) -> str:
    """fields are already rendered as 'path (kind, kind)'; kinds are unattributed."""
    if fields:
        return f"Mnemonic rejected the input. Check: {', '.join(fields)}."
    if kinds:
        return (
            f"Mnemonic rejected the input ({', '.join(kinds)}). "
            "Check the field names and constraints."
        )
    return "Mnemonic rejected the input. Check the field names and constraints."


@pytest.fixture
def settings():
    return Settings(api_key=API_KEY, api_url="http://api:8000")


@pytest.fixture
def project():
    return {
        "id": PROJECT_ID, "name": "Example", "slug": "example", "description": "A test project",
        "repository_url": None, "created_at": NOW, "updated_at": NOW,
    }


@pytest.fixture
def work_item():
    return {
        "id": WORK_ID,
        "project_id": PROJECT_ID,
        "title": "Investigate empty results",
        "summary": "Recall loses a result when its session ID contains punctuation.",
        "status": "open",
        "priority": 7,
        "initial_checkpoint_id": CHECKPOINT_ID,
        "version": 3,
        "created_at": NOW,
        "updated_at": NOW,
    }


@pytest.fixture
def checkpoint():
    return {
        "id": CHECKPOINT_ID,
        "work_item_id": WORK_ID,
        "kind": "context",
        "prompt": "Agent-authored context.\n\nInspect the query and verify the result.\n",
        "source_client": "claude-code",
        "source_session_id": "d62227f0-73af-4a11-83bc-cdfc3957761c",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": "main",
        "verified_against": None,
        "tags": ["search"],
        "source_metadata": {"author_verified": False},
        "migration_origin": None,
        "legacy_record_id": None,
        "created_at": NOW,
    }


@pytest.fixture
def readiness():
    return {
        "lifecycle_status": "open",
        "is_terminal": False,
        "has_active_lease": False,
        "active_lease": None,
        "unresolved_blocker_count": 0,
        "is_blocked": False,
        "is_ready": True,
        "display_state": "ready",
    }


@pytest.fixture
def relationship():
    return {
        "id": RELATIONSHIP_ID,
        "project_id": PROJECT_ID,
        "relationship_type": "blocks",
        "source_work_item_id": OTHER_WORK_ID,
        "target_work_item_id": WORK_ID,
        "context_checkpoint_work_item_id": None,
        "context_checkpoint_id": None,
        "created_by_client": "claude-code",
        "created_by_session_id": "relationship-session",
        "created_by_model": "test-model",
        "created_at": NOW,
    }


@pytest.fixture
def adjacent_relationship(relationship, readiness):
    return {
        "relationship": relationship,
        "relative_to_work_item_id": WORK_ID,
        "direction": "incoming",
        "counterpart": {
            "id": OTHER_WORK_ID,
            "title": "Prepare prerequisite",
            "status": "open",
            "readiness": readiness,
        },
    }


@pytest.fixture
def work_summary(work_item, checkpoint, readiness):
    pointer = {
        name: value
        for name, value in checkpoint.items()
        if name not in {"prompt", "source_metadata", "source_session_url"}
    }
    return {
        "work_item": work_item,
        "checkpoint_count": 1,
        "ancestor_path": [],
        "ancestor_path_truncated": False,
        "current_context": pointer,
        "readiness": readiness,
    }


@pytest.fixture
def work_context(work_item, checkpoint, readiness):
    return {
        "work_item": work_item,
        "initial_checkpoint": checkpoint,
        # Recall serializes a single-checkpoint item's body once.
        "current_context": None,
        "current_context_is_initial": True,
        "recent_checkpoints": [],
        "checkpoint_total": 1,
        "omitted_checkpoint_count": 0,
        "recent_events": [],
        "event_total": 0,
        "omitted_event_count": 0,
        "pre_phase5_history_may_be_incomplete": False,
        "readiness": readiness,
        "incoming_relationships": [],
        "outgoing_relationships": [],
        "undirected_relationships": [],
        "relationship_counts": {"incoming": 0, "outgoing": 0, "undirected": 0, "total": 0},
    }


@pytest.fixture
def progress_event():
    return {
        "id": 42,
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "event_type": "progress",
        "actor_kind": "client",
        "actor_client": "claude-code",
        "actor_session_id": "phase-5-session",
        "actor_model": "test-model",
        "body": "Investigated the race; the focused test now passes.",
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
        "metadata": {"tests": ["focused"], "percent": 50},
        "origin": "live",
        "created_at": NOW,
    }


@pytest.fixture
def same_status_update_event(progress_event):
    return {
        **progress_event,
        "id": 43,
        "event_type": "work_updated",
        "body": None,
        "metadata": {
            "changes": {"status": {"before": "open", "after": "open"}},
            "work_version": 4,
        },
    }


@pytest.fixture
def claim_receipt():
    return {
        "work_item_id": WORK_ID,
        "holder_client": "claude-code",
        "holder_session_id": "claiming-session",
        "claim_request_id": CLAIM_REQUEST_ID,
        "acquired_at": NOW,
        "renewed_at": NOW,
        "expires_at": EXPIRES_AT,
        "lease_token": LEASE_TOKEN,
    }


@pytest.fixture
def active_work_context(work_context, claim_receipt):
    public_lease = {
        name: claim_receipt[name]
        for name in (
            "holder_client",
            "holder_session_id",
            "acquired_at",
            "renewed_at",
            "expires_at",
        )
    }
    return {
        **work_context,
        "readiness": {
            **work_context["readiness"],
            "has_active_lease": True,
            "active_lease": public_lease,
            "is_ready": False,
            "display_state": "active",
        },
    }
