import pytest

from mnemonic_mcp.config import Settings

API_KEY = "mnemonic-test-secret-" + "x" * 32
PROJECT_ID = "ca501b3f-860b-4f88-bca8-2f22a06359ab"
HANDOFF_ID = "9b0e1e53-2a04-443d-a37c-d0a19cf760a8"
WORK_ID = HANDOFF_ID
CHECKPOINT_ID = "74ce5a36-7295-45e7-bc24-5aa13ed4f293"
NOW = "2026-08-30T12:00:00Z"


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
def handoff():
    return {
        "id": HANDOFF_ID, "project_id": PROJECT_ID, "title": "Investigate empty results",
        "summary": "Recall loses a result when its session ID contains punctuation.",
        "prompt": "Agent-authored proposal.\n\nInspect the query.\n\nVerify a UUID session is found.\n",
        "source_client": "claude-code", "source_session_id": "d62227f0-73af-4a11-83bc-cdfc3957761c",
        "source_model": None, "source_session_url": None, "repository_branch": "main",
        "verified_against": None, "source_metadata": {"author_verified": False},
        "status": "open", "tags": ["search"], "version": 3,
        "created_at": NOW, "updated_at": NOW,
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
        "current_context": checkpoint,
        "recent_checkpoints": [],
        "checkpoint_total": 1,
        "omitted_checkpoint_count": 0,
        "readiness": readiness,
        "incoming_relationships": [],
        "outgoing_relationships": [],
        "undirected_relationships": [],
        "relationship_counts": {"incoming": 0, "outgoing": 0, "undirected": 0, "total": 0},
    }
