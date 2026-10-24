from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from mnemonic_api.config import Settings
from mnemonic_api.live_sync import MutationEvent, mutation_event
from mnemonic_api.main import create_app

API_KEY = "live-sync-test-key-with-at-least-32-characters"
DATABASE_URL = "postgresql://localhost:1/unavailable"


def settings(**changes) -> Settings:
    return Settings(database_url=DATABASE_URL, api_key=API_KEY, **changes)


def test_dashboard_origins_are_canonical_and_exact():
    configured = settings(
        dashboard_origins=(
            " HTTPS://Example.COM:443/, http://localhost:3000, "
            "http://localhost:3000"
        )
    )
    assert configured.allowed_dashboard_origins == frozenset(
        {"https://example.com", "http://localhost:3000"}
    )

    for invalid in [
        "",
        "http://localhost:3000,",
        "*",
        "https://*.example.com",
        "file:///tmp/dashboard",
        "https://user:password@example.com",
        "https://example.com/path",
        "https://example.com?query=yes",
        "https://example.com#fragment",
        "https://example.com:not-a-port",
    ]:
        with pytest.raises(ValidationError):
            settings(dashboard_origins=invalid)


def test_mutation_events_are_scoped_without_record_contents():
    project_id = str(uuid4())
    work_item_id = str(uuid4())

    assert mutation_event(
        "PUT", f"/api/v1/projects/{project_id}/knowledge/decisions"
    ) == MutationEvent("work-items")
    assert mutation_event(
        "POST", f"/api/v1/projects/{project_id}/knowledge/changes"
    ) == MutationEvent("work-items")
    assert mutation_event("PUT", f"/api/v1/projects/{project_id}/work-items") is None

    assert mutation_event("POST", "/api/v1/projects") == MutationEvent("projects")
    assert mutation_event(
        "PATCH", f"/api/v1/projects/{project_id}"
    ) == MutationEvent("projects")
    assert mutation_event(
        "PATCH", f"/api/v1/projects/{project_id}/settings"
    ) == MutationEvent("projects")
    assert mutation_event(
        "POST", f"/api/v1/projects/{project_id}/work-items"
    ) == MutationEvent("work-items")
    assert mutation_event(
        "POST",
        f"/api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    ) == MutationEvent("work-items")
    assert mutation_event(
        "DELETE", f"/api/v1/projects/{project_id}/relationships/{uuid4()}"
    ) == MutationEvent("work-items")
    assert mutation_event(
        "POST", f"/api/v1/projects/{project_id}/work-items/{work_item_id}/delete"
    ) == MutationEvent("work-items")
    assert mutation_event(
        "PATCH", f"/api/v1/projects/{project_id}/work-items/{work_item_id}"
    ) == MutationEvent("work-items")

    assert MutationEvent("work-items").message(9) == {
        "type": "invalidate",
        "revision": 9,
        "scope": "work-items",
    }

    assert mutation_event("GET", "/api/v1/projects") is None
    assert mutation_event("POST", "/healthz") is None
    assert mutation_event("GET", f"/api/v1/projects/{project_id}/settings") is None
    assert mutation_event("POST", f"/api/v1/projects/{project_id}/settings") is None
    assert mutation_event(
        "POST", f"/api/v1/projects/{project_id}/duplicate-suggestions"
    ) is None
    assert mutation_event("POST", "/api/v1/projects/not-a-uuid/work-items") is None
    assert mutation_event("POST", f"/api/v1/projects/{project_id}/unknown") is None


@pytest.mark.parametrize("path", ["/api/v1/sync", "/api/mnemonic/sync"])
def test_live_sync_accepts_only_configured_browser_origins(path):
    app = create_app(
        settings(dashboard_origins="http://localhost:3000,https://mnemonic.example")
    )
    with TestClient(app) as client:
        with client.websocket_connect(
            path, headers={"origin": "http://localhost:3000"}
        ) as websocket:
            assert websocket.receive_json() == {"type": "ready", "revision": 0}

        for origin in [None, "https://attacker.example", "null"]:
            headers = {} if origin is None else {"origin": origin}
            with pytest.raises(WebSocketDisconnect) as denied:
                with client.websocket_connect(path, headers=headers):
                    pass
            assert denied.value.code == 1008


def test_browser_and_machine_sync_share_the_same_data_free_invalidation():
    app = create_app(settings())
    with TestClient(app) as client:
        with (
            client.websocket_connect(
                "/api/v1/sync", headers={"origin": "http://localhost:3000"}
            ) as machine,
            client.websocket_connect(
                "/api/mnemonic/sync", headers={"origin": "http://localhost:3000"}
            ) as browser,
        ):
            assert machine.receive_json() == {"type": "ready", "revision": 0}
            assert browser.receive_json() == {"type": "ready", "revision": 0}
            assert client.portal is not None
            client.portal.call(app.state.live_sync_hub.publish, MutationEvent("work-items"))
            expected = {"type": "invalidate", "revision": 1, "scope": "work-items"}
            assert machine.receive_json() == expected
            assert browser.receive_json() == expected
