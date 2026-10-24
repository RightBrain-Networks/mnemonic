"""Change scope keeps exact provenance, version fences and retry semantics."""

import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import ACTOR, ASSESSMENT_ID, assessment
from test_knowledge_tools import arguments, decision
from test_tools import adapter, structured

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME

CHANGE_ID = "97513127-57bc-41a2-889f-07387a8d345a"


def change(**overrides):
    return {
        "id": CHANGE_ID, "project_id": PROJECT_ID, "origin_reference": "ADR-00001-1",
        "review_work_item_id": WORK_ID, "parent_change_id": None, "scope_version": 2,
        "review_work_project_id": PROJECT_ID, "review_closed": False,
        "successor_change_id": None,
        "acknowledged_scope_version": 2, "assessment_count": 2, "revision_count": 2,
        "pending_revision_count": 0,
        "created_at": NOW, "updated_at": NOW, **overrides,
    }


async def test_change_reads_bind_identity_and_filter_current_episodes(settings):
    paths = []

    def handler(request):
        paths.append(str(request.url))
        return stream_json(
            {"items": [assessment()], "total": 1, "limit": 50, "offset": 0}
            if request.url.path.endswith("assessments") else change()
        )

    server = adapter(settings, handler)
    result = structured(await server.call_tool("decision_change_get", {
        "project_id": PROJECT_ID, "change_id": CHANGE_ID,
    }))
    assert result["scope_version"] == 2
    await server.call_tool("decision_assessments", {
        "project_id": PROJECT_ID, "change_id": CHANGE_ID,
    })
    assert paths[0].endswith(f"/knowledge/changes/{CHANGE_ID}")
    assert f"change_id={CHANGE_ID}" in paths[1]


async def test_moved_driver_and_closed_successor_state_survive_reads(settings):
    expected = change(review_work_project_id=WORK_ID, review_closed=True,
                      successor_change_id=ASSESSMENT_ID)
    server = adapter(settings, lambda request: stream_json(expected))
    result = structured(await server.call_tool("decision_change_get", {
        "project_id": PROJECT_ID, "change_id": CHANGE_ID,
    }))
    assert result == expected


@pytest.mark.parametrize("action,fields", [
    ("acknowledge", {"change_id": CHANGE_ID, "expected_scope_version": 2}),
    ("adopt", {"work_item_id": WORK_ID, "expected_work_version": 3,
               "assessment_ids": [ASSESSMENT_ID]}),
])
async def test_actions_forward_exact_fenced_intent(settings, action, fields):
    bodies = []

    def handler(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/knowledge/changes")
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=change())

    server = adapter(settings, handler)
    intent = {"project_id": PROJECT_ID, "action": action, **fields, **ACTOR,
              "lease_token": "opaque-lease-capability", "client_operation_id": CLIENT_OPERATION_ID}
    await server.call_tool("decision_change_update", intent)
    await server.call_tool("decision_change_update", intent)
    assert bodies[0] == bodies[1]
    assert bodies[0]["lease_token"] == "opaque-lease-capability"
    assert all(bodies[0][key] == value for key, value in fields.items())


@pytest.mark.parametrize("fields", [
    {"action": "admit", "change_id": CHANGE_ID, "expected_scope_version": 2},
    {"action": "acknowledge", "change_id": CHANGE_ID},
    {"action": "adopt", "work_item_id": WORK_ID, "expected_work_version": 1},
    {"action": "adopt", "work_item_id": WORK_ID, "expected_work_version": 1,
     "assessment_ids": [ASSESSMENT_ID, ASSESSMENT_ID]},
])
async def test_agent_cannot_admit_or_send_unfenced_actions(settings, fields):
    server = adapter(settings, lambda request: pytest.fail("Invalid action reached HTTP"))
    with pytest.raises(ToolError):
        await server.call_tool("decision_change_update", {
            "project_id": PROJECT_ID, **ACTOR, **fields,
            "client_operation_id": CLIENT_OPERATION_ID,
        })


async def test_wrong_acknowledgement_response_keeps_unknown_outcome(settings):
    server = adapter(settings, lambda request: httpx.Response(
        200, json=change(acknowledged_scope_version=1),
    ))
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await server.call_tool("decision_change_update", {
            "project_id": PROJECT_ID, "action": "acknowledge", "change_id": CHANGE_ID,
            "expected_scope_version": 2, **ACTOR, "client_operation_id": CLIENT_OPERATION_ID,
        })


async def test_adoption_without_the_requested_assessment_keeps_unknown_outcome(settings):
    server = adapter(settings, lambda request: httpx.Response(
        200, json=change(scope_version=0, acknowledged_scope_version=0, assessment_count=0),
    ))
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await server.call_tool("decision_change_update", {
            "project_id": PROJECT_ID, "action": "adopt", "work_item_id": WORK_ID,
            "expected_work_version": 1, "assessment_ids": [ASSESSMENT_ID], **ACTOR,
            "client_operation_id": CLIENT_OPERATION_ID,
        })


async def test_decision_put_keeps_explicit_origin_and_classification(settings):
    declaration = {"kind": "record-existing", "change_id": CHANGE_ID,
                   "reason": "Records the same owner ruling", "governing_reference": "ADR-00001-0"}

    def handler(request):
        body = json.loads(request.content)
        assert body["source_work_item_id"] == WORK_ID
        assert body["change"] == declaration
        return httpx.Response(200, json={"created": True, "decision": decision(
            change={**declaration, "source_work_item_id": WORK_ID},
        )})

    await adapter(settings, handler).call_tool("decision_put", {
        **arguments(), "source_work_item_id": WORK_ID, "change": declaration,
    })


@pytest.mark.parametrize("classification", [
    {"kind": "maintenance"}, {"kind": "record-existing", "reason": "Same ruling"},
    {"kind": "substantive", "governing_reference": "ADR-00001-0"},
])
async def test_non_substantive_classification_requires_evidence(settings, classification):
    server = adapter(settings, lambda request: pytest.fail("Invalid declaration reached HTTP"))
    with pytest.raises(ToolError):
        await server.call_tool("decision_put", {
            **arguments(), "source_work_item_id": None, "change": classification,
        })


async def test_historical_sparse_put_does_not_invent_external_origin(settings):
    def handler(request):
        assert "source_work_item_id" not in json.loads(request.content)
        assert "change" not in json.loads(request.content)
        return httpx.Response(200, json={"created": True, "decision": decision()})

    await adapter(settings, handler).call_tool("decision_put", arguments())


async def test_unchanged_supersession_declaration_survives_transport_and_response(settings):
    entries = [{"target_reference": "ADR-00002-0", "governing_reference": "ADR-00001-0",
                "reason": "Only an unrelated rule changed; this supersession is unchanged."}]
    declaration = {"kind": "substantive", "change_id": None, "reason": None,
                   "governing_reference": None, "unchanged_supersessions": entries}

    def handler(request):
        assert json.loads(request.content)["change"] == declaration
        return httpx.Response(200, json={"created": True, "decision": decision(change={
            **declaration, "change_id": CHANGE_ID, "reason": None, "governing_reference": None,
            "source_work_item_id": None,
        })})

    result = structured(await adapter(settings, handler).call_tool("decision_put", {
        **arguments(), "source_work_item_id": None, "change": declaration,
    }))
    assert result["decision"]["change"]["unchanged_supersessions"] == entries


@pytest.mark.parametrize("entries", [
    [{"target_reference": "ADR-00002", "governing_reference": "ADR-00001-0", "reason": "Same"}],
    [{"target_reference": "ADR-00002-0", "governing_reference": "ADR-00001", "reason": "Same"}],
    [{"target_reference": "ADR-00002-0", "governing_reference": "ADR-00001-0", "reason": " "}],
    [{"target_reference": f"ADR-00002-{revision}", "governing_reference": "ADR-00001-0",
      "reason": "Duplicate target"} for revision in (0, 1)],
])
async def test_unchanged_supersession_declarations_require_exact_unique_evidence(settings, entries):
    server = adapter(settings, lambda request: pytest.fail("Invalid declaration reached HTTP"))
    with pytest.raises(ToolError):
        await server.call_tool("decision_put", {
            **arguments(), "source_work_item_id": None,
            "change": {"unchanged_supersessions": entries},
        })


async def test_missing_retained_supersession_declaration_is_unknown_outcome(settings):
    server = adapter(settings, lambda request: httpx.Response(200, json={
        "created": True, "decision": decision(change={
            "kind": "substantive", "change_id": CHANGE_ID, "reason": None,
            "governing_reference": None, "source_work_item_id": None,
        }),
    }))
    with pytest.raises(ToolError, match=UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME):
        await server.call_tool("decision_put", {
            **arguments(), "source_work_item_id": None, "change": {
                "unchanged_supersessions": [{"target_reference": "ADR-00002-0",
                    "governing_reference": "ADR-00001-0", "reason": "Same supersession rule"}],
            },
        })
