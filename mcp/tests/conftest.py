import pytest

from mnemonic_mcp.config import Settings

API_KEY = "mnemonic-test-secret-" + "x" * 32
PROJECT_ID = "ca501b3f-860b-4f88-bca8-2f22a06359ab"
HANDOFF_ID = "9b0e1e53-2a04-443d-a37c-d0a19cf760a8"
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
