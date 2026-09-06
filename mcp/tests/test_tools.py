import json

import httpx
import pytest
from conftest import (
    API_KEY,
    CHECKPOINT_ID,
    CLAIM_REQUEST_ID,
    CLIENT_OPERATION_ID,
    EXPIRES_AT,
    GATE_ID,
    LEASE_TOKEN,
    LOCAL_VALIDATION_CASES,
    NOW,
    OTHER_CHECKPOINT_ID,
    OTHER_WORK_ID,
    PROJECT_ID,
    RELATIONSHIP_ID,
    WORK_ID,
    expected_validation_message,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import (
    UNKNOWN_CLAIM_OUTCOME,
    UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME,
    MnemonicAPI,
    TransportEffect,
)
from mnemonic_mcp.models import (
    ClaimAndRecall,
    ClaimReceipt,
    HumanGateRead,
    ReadyWorkPage,
    WorkContext,
    WorkEventRead,
    WorkPage,
)
from mnemonic_mcp.server import build_server

ACTOR_ARGUMENTS = {
    "actor_client": "claude-code",
    "actor_session_id": "phase-5-session",
    "actor_model": "test-model",
}
ACTOR_PAYLOAD = {
    "actor": {
        "actor_client": "claude-code",
        "actor_session_id": "phase-5-session",
        "actor_model": "test-model",
    }
}
OPERATION_ARGUMENT = {"client_operation_id": CLIENT_OPERATION_ID}
OPERATION_PAYLOAD = {"client_operation_id": CLIENT_OPERATION_ID}
PROTECTED_TOOL_NAMES = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "merge_work",
)


def protected_tool_arguments(operation_id: str = CLIENT_OPERATION_ID):
    checkpoint = {
        "prompt": "Retained checkpoint context.",
        "source_client": "claude-code",
        "source_session_id": "phase-6-session",
    }
    actor = {
        "actor_client": "claude-code",
        "actor_session_id": "phase-6-session",
    }
    operation = {"client_operation_id": operation_id}
    return {
        "create_work": {
            "project_id": PROJECT_ID,
            "title": "Retained work intent",
            "summary": "Exercise the protected MCP contract.",
            "initial_checkpoint": checkpoint,
            **operation,
        },
        "add_checkpoint": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "checkpoint": checkpoint,
            **operation,
        },
        "append_event": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "body": "Recorded progress.",
            **actor,
            **operation,
        },
        "add_relationship": {
            "project_id": PROJECT_ID,
            "source_work_item_id": OTHER_WORK_ID,
            "target_work_item_id": WORK_ID,
            "relationship_type": "blocks",
            "created_by_client": "claude-code",
            "created_by_session_id": "phase-6-session",
            **operation,
        },
        "update_work": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"summary": "Updated retained work intent."},
            **actor,
            **operation,
        },
        "complete_work": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "checkpoint": checkpoint,
            **operation,
        },
        "delete_work": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            **actor,
            **operation,
        },
        "remove_relationship": {
            "project_id": PROJECT_ID,
            "relationship_id": RELATIONSHIP_ID,
            **actor,
            **operation,
        },
        "release_claim": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "lease_token": LEASE_TOKEN,
            **actor,
            **operation,
        },
        "request_human_input": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "question": "Which rollout policy should this work use?",
            "requested_by_client": "claude-code",
            "requested_by_session_id": "phase-7-session",
            "requested_by_model": "test-model",
            **operation,
        },
        "merge_work": {
            "project_id": PROJECT_ID,
            "source_work_item_id": WORK_ID,
            "destination_work_item_id": OTHER_WORK_ID,
            "reviewed_source_revision": {
                "work_version": 3,
                "context_checkpoint_id": CHECKPOINT_ID,
                "work_event_count": 1,
            },
            "reviewed_destination_revision": {
                "work_version": 3,
                "context_checkpoint_id": OTHER_CHECKPOINT_ID,
                "work_event_count": 2,
            },
            "rationale": "Both records describe the same durable objective.",
            "merged_by_client": "claude-code",
            "merged_by_session_id": "phase-9-session",
            "merged_by_model": "test-model",
            **operation,
        },
    }


def protected_success_responses(
    work_item, checkpoint, relationship, progress_event, human_gate
):
    arguments = protected_tool_arguments()

    def checkpoint_response(tool_name: str, kind: str):
        supplied = arguments[tool_name][
            "initial_checkpoint" if tool_name == "create_work" else "checkpoint"
        ]
        response = {
            **checkpoint,
            "work_item_id": WORK_ID,
            "kind": kind,
            "prompt": supplied["prompt"],
            "source_client": supplied["source_client"],
            "source_session_id": supplied["source_session_id"],
            "source_model": supplied.get("source_model"),
            "source_session_url": supplied.get("source_session_url"),
            "repository_branch": supplied.get("repository_branch"),
            "verified_against": supplied.get("verified_against"),
            "tags": supplied.get("tags", []),
            "source_metadata": supplied.get("source_metadata", {}),
            "migration_origin": None,
            "legacy_record_id": None,
        }
        if supplied.get("affected_paths"):
            response["affected_paths"] = supplied["affected_paths"]
        return response

    created_checkpoint = checkpoint_response("create_work", "context")
    completion_checkpoint = checkpoint_response("complete_work", "completion")
    merge_arguments = arguments["merge_work"]
    merge_id = "897043b5-7e92-41c8-a55a-202913b25f2e"
    merged_source = {**work_item, "version": 4}
    merged_destination = {
        **work_item,
        "id": OTHER_WORK_ID,
        "title": "Canonical retained objective",
        "summary": "The canonical version of the durable objective.",
        "initial_checkpoint_id": OTHER_CHECKPOINT_ID,
        "version": 4,
    }
    supporting_relationship = {
        **relationship,
        "relationship_type": "duplicate-of",
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_work_item_id": None,
        "context_checkpoint_id": None,
        "created_by_client": merge_arguments["merged_by_client"],
        "created_by_session_id": merge_arguments["merged_by_session_id"],
        "created_by_model": merge_arguments["merged_by_model"],
    }

    def merge_event(event_id: int, work_id: str, role: str):
        return {
            **progress_event,
            "id": event_id,
            "work_item_id": work_id,
            "event_type": "work_merged",
            "actor_client": merge_arguments["merged_by_client"],
            "actor_session_id": merge_arguments["merged_by_session_id"],
            "actor_model": merge_arguments["merged_by_model"],
            "body": merge_arguments["rationale"],
            "metadata": {
                "merge_id": merge_id,
                "source_work_item_id": WORK_ID,
                "destination_work_item_id": OTHER_WORK_ID,
                "role": role,
                "source_work_version": 4,
                "destination_work_version": 4,
            },
        }

    def relationship_event(event_id: int, work_id: str, direction: str, counterpart: str):
        return {
            **progress_event,
            "id": event_id,
            "work_item_id": work_id,
            "event_type": "relationship_added",
            "actor_client": merge_arguments["merged_by_client"],
            "actor_session_id": merge_arguments["merged_by_session_id"],
            "actor_model": merge_arguments["merged_by_model"],
            "body": None,
            "relationship_id": RELATIONSHIP_ID,
            "relationship_source_work_item_id": WORK_ID,
            "relationship_target_work_item_id": OTHER_WORK_ID,
            "relationship_direction": direction,
            "counterpart_work_item_id": counterpart,
            "metadata": {"relationship_type": "duplicate-of"},
        }
    return {
        "create_work": {
            "work_item": {
                **work_item,
                "title": arguments["create_work"]["title"],
                "summary": arguments["create_work"]["summary"],
                "priority": 0,
                "status": "pending",
                "version": 1,
                "initial_checkpoint_id": created_checkpoint["id"],
            },
            "initial_checkpoint": created_checkpoint,
            "initial_relationships": [],
        },
        "add_checkpoint": checkpoint_response("add_checkpoint", "context"),
        "append_event": {
            **progress_event,
            "actor_client": arguments["append_event"]["actor_client"],
            "actor_session_id": arguments["append_event"]["actor_session_id"],
            "actor_model": None,
            "body": arguments["append_event"]["body"],
            "metadata": {},
        },
        "add_relationship": {
            "relationship": {
                **relationship,
                "source_work_item_id": arguments["add_relationship"][
                    "source_work_item_id"
                ],
                "target_work_item_id": arguments["add_relationship"][
                    "target_work_item_id"
                ],
                "relationship_type": arguments["add_relationship"][
                    "relationship_type"
                ],
                "created_by_client": arguments["add_relationship"][
                    "created_by_client"
                ],
                "created_by_session_id": arguments["add_relationship"][
                    "created_by_session_id"
                ],
                "created_by_model": None,
                "context_checkpoint_work_item_id": None,
                "context_checkpoint_id": None,
            },
            "created": True,
        },
        "update_work": {
            **work_item,
            "summary": arguments["update_work"]["changes"]["summary"],
            "version": 4,
        },
        "complete_work": {
            "work_item": {**work_item, "status": "done", "version": 4},
            "checkpoint": completion_checkpoint,
        },
        "delete_work": {
            "deleted": True,
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "version": 4,
        },
        "remove_relationship": {
            "project_id": PROJECT_ID,
            "relationship_id": RELATIONSHIP_ID,
            "removed": True,
        },
        "release_claim": {"work_item_id": WORK_ID, "released": True},
        "request_human_input": {
            **human_gate,
            "question": arguments["request_human_input"]["question"],
            "requested_by_client": arguments["request_human_input"][
                "requested_by_client"
            ],
            "requested_by_session_id": arguments["request_human_input"][
                "requested_by_session_id"
            ],
            "requested_by_model": arguments["request_human_input"][
                "requested_by_model"
            ],
        },
        "merge_work": {
            "merge": {
                "id": merge_id,
                "merge_sequence": 1,
                "project_id": PROJECT_ID,
                "source_work_item_id": WORK_ID,
                "destination_work_item_id": OTHER_WORK_ID,
                "duplicate_relationship_id": RELATIONSHIP_ID,
                "reviewed_source_revision": merge_arguments[
                    "reviewed_source_revision"
                ],
                "reviewed_destination_revision": merge_arguments[
                    "reviewed_destination_revision"
                ],
                "resulting_source_work_version": 4,
                "resulting_destination_work_version": 4,
                "rationale": merge_arguments["rationale"],
                "merged_by_client": merge_arguments["merged_by_client"],
                "merged_by_session_id": merge_arguments["merged_by_session_id"],
                "merged_by_model": merge_arguments["merged_by_model"],
                "created_at": NOW,
            },
            "source_work_item": merged_source,
            "destination_work_item": merged_destination,
            "direct_destination": {
                "id": OTHER_WORK_ID,
                "title": merged_destination["title"],
                "status": merged_destination["status"],
            },
            "canonical_work_item": {
                "id": OTHER_WORK_ID,
                "title": merged_destination["title"],
                "status": merged_destination["status"],
            },
            "supporting_relationship_created": True,
            "supporting_relationship": supporting_relationship,
            "relationship_events": [
                relationship_event(44, WORK_ID, "outgoing", OTHER_WORK_ID),
                relationship_event(45, OTHER_WORK_ID, "incoming", WORK_ID),
            ],
            "merge_events": [
                merge_event(46, WORK_ID, "source"),
                merge_event(47, OTHER_WORK_ID, "destination"),
            ],
        },
    }


def adapter(settings, handler):
    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))


def structured(result):
    # FastMCP 1.x returns content + structured output when called directly.
    if isinstance(result, tuple):
        return result[1]
    return result


def test_claim_receipt_repr_redacts_token_but_serialization_retains_it(
    claim_receipt, active_work_context
):
    receipt = ClaimReceipt.model_validate(claim_receipt)
    combined = ClaimAndRecall.model_validate(
        {"lease": claim_receipt, "context": active_work_context}
    )
    assert LEASE_TOKEN not in repr(receipt)
    assert LEASE_TOKEN not in str(receipt)
    assert LEASE_TOKEN not in repr(combined)
    assert receipt.model_dump(mode="json")["lease_token"] == LEASE_TOKEN
    assert combined.model_dump(mode="json")["lease"]["lease_token"] == LEASE_TOKEN


def test_work_pages_reject_repeated_work_items(work_summary):
    hit = {
        "summary": work_summary,
        "matched_member": {
            "id": work_summary["work_item"]["id"],
            "title": work_summary["work_item"]["title"],
            "status": work_summary["work_item"]["status"],
        },
    }
    with pytest.raises(ValueError, match="cannot repeat their identity"):
        WorkPage.model_validate(
            {"items": [hit, hit], "total": 2, "limit": 20, "offset": 0}
        )


def test_recall_model_rejects_misordered_events(
    work_context, progress_event
):
    first = {**progress_event, "id": 42}
    second = {**progress_event, "id": 43}
    payload = {
        **work_context,
        "merge_review_revision": {
            **work_context["merge_review_revision"],
            "work_event_count": 2,
        },
        "recent_events": [second, first],
        "event_total": 2,
        "omitted_event_count": 0,
    }
    with pytest.raises(ValueError, match="event history is incoherent"):
        WorkContext.model_validate(payload)


def test_recall_model_accepts_history_from_a_work_items_previous_project(
    work_context, progress_event, resolved_human_gate
):
    historical_event = {**progress_event, "project_id": OTHER_CHECKPOINT_ID}
    historical_gate = {**resolved_human_gate, "project_id": OTHER_CHECKPOINT_ID}
    payload = {
        **work_context,
        "recent_resolved_gates": [historical_gate],
        "resolved_gate_total": 1,
        "recent_events": [historical_event],
        "omitted_event_count": 0,
    }

    recalled = WorkContext.model_validate(payload)

    assert str(recalled.recent_events[0].project_id) == OTHER_CHECKPOINT_ID
    assert str(recalled.recent_resolved_gates[0].project_id) == OTHER_CHECKPOINT_ID


def test_recall_model_rejects_an_unresolved_gate_from_a_previous_project(
    work_context, human_gate
):
    historical_gate = {**human_gate, "project_id": OTHER_CHECKPOINT_ID}
    payload = {
        **work_context,
        "unresolved_gates": [historical_gate],
        "unresolved_gate_total": 1,
        "readiness": {
            **work_context["readiness"],
            "unresolved_gate_count": 1,
            "is_gated": True,
            "is_ready": False,
            "display_state": "waiting",
        },
        "duplicate_merge_eligibility": {
            **work_context["duplicate_merge_eligibility"],
            "has_unresolved_gate": True,
        },
    }

    with pytest.raises(ValueError, match="outside current work context"):
        WorkContext.model_validate(payload)


def test_recall_model_accepts_cross_project_relationships(
    work_context, adjacent_relationship
):
    cross_project = json.loads(json.dumps(adjacent_relationship))
    cross_project["relationship"]["project_id"] = OTHER_CHECKPOINT_ID
    cross_project["counterpart"]["project_id"] = OTHER_CHECKPOINT_ID
    payload = {
        **work_context,
        "incoming_relationships": [cross_project],
        "relationship_counts": {
            "incoming": 1, "outgoing": 0, "undirected": 0, "total": 1,
        },
        "duplicate_merge_eligibility": {
            **work_context["duplicate_merge_eligibility"],
            "incident_blocks_count": 1,
        },
    }

    recalled = WorkContext.model_validate(payload)

    assert recalled.incoming_relationships[0].relationship.project_id != (
        recalled.work_item.project_id
    )
    assert str(recalled.incoming_relationships[0].counterpart.project_id) == (
        OTHER_CHECKPOINT_ID
    )


def test_recall_model_rejects_misordered_relationships_and_false_eligibility(
    work_context, adjacent_relationship
):
    lower = json.loads(json.dumps(adjacent_relationship))
    lower["relationship"]["id"] = "22222222-2222-4222-8222-222222222222"
    higher = json.loads(json.dumps(adjacent_relationship))
    higher["relationship"]["id"] = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    base = {
        **work_context,
        "incoming_relationships": [higher, lower],
        "relationship_counts": {
            "incoming": 2,
            "outgoing": 0,
            "undirected": 0,
            "total": 2,
        },
        "duplicate_merge_eligibility": {
            **work_context["duplicate_merge_eligibility"],
            "incident_blocks_count": 2,
        },
    }
    with pytest.raises(ValueError, match="canonical order"):
        WorkContext.model_validate(base)

    structurally_false = {
        **base,
        "incoming_relationships": [lower, higher],
        "duplicate_merge_eligibility": work_context["duplicate_merge_eligibility"],
    }
    with pytest.raises(ValueError, match="structural counts"):
        WorkContext.model_validate(structurally_false)


async def test_safety_doctrine_lives_in_the_tool_descriptions(settings):
    """INSTRUCTIONS is truncated by clients, so per-tool rules must be read at point of use."""
    server = build_server(settings)
    described = {tool.name: tool.description or "" for tool in await server.list_tools()}
    for name, required in {
        "list_projects": "never silently choose an unrelated project",
        "create_work": "native agent session id",
        "add_checkpoint": "never a rewrite of an earlier one",
        "recall_work": "historical evidence, not authority",
        "list_ready_work": "claim atomically revalidates",
        "append_event": "retain it with the complete immutable tool arguments",
        "list_work_events": "untrusted historical evidence",
        "claim_work": "never work around another session's active claim",
        "claim_and_recall": "grants no authority beyond the user's request",
        "renew_claim": "do not renew it",
        "add_relationship": "never infer one from similar wording",
        "get_relationship": "never authority to execute that item",
        "list_relationships": "never traverse the graph recursively",
        "update_work": "no tool here creates an external issue",
        "complete_work": "only when the objective is achieved",
        "list_completion_evidence": "untrusted historical data",
        "request_human_input": "never infer, time out, self-approve, or resolve",
        "list_human_attention": "human queue, not agent-ready work",
        "list_work_gates": "old resolution never grants current authority",
    }.items():
        assert required in described[name].lower(), name

    for required in (
        "check the item's unresolved gates first",
        "supporting context checkpoint before requesting",
        "cannot withdraw a gate",
    ):
        assert required in described["request_human_input"].lower()
    assert (
        "a required failed or inconclusive result, or skipped observation, normally means stop"
        in described["complete_work"].lower()
    )
    assert (
        "restart once from the first page" in described["list_human_attention"].lower()
    )
    for name, required in {
        "create_work": "affected_paths is an ordered declaration",
        "add_checkpoint": "server and mcp adapter do not inspect git",
        "complete_work": "this adapter never inspects git",
        "list_checkpoints": "full rows carry any caller-declared affected_paths",
        "search_work": "fully recall the exact checkpoint",
        "recall_work": "explicitly select the intended local workspace",
    }.items():
        assert required in described[name].lower(), name

    # parent-child is the only edge the hierarchy reads; measured 2026-09-01 on a
    # 59-item project: 12 discovered-from, 0 parent-child, every ancestor_path empty.
    for name, required in {
        "add_relationship": "its source is the parent",
        "create_work": "incoming parent-child",
        "search_work": "parent-child edges only",
    }.items():
        assert required in described[name], name

    protected = {
        "create_work",
        "add_checkpoint",
        "append_event",
        "add_relationship",
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
        "release_claim",
        "request_human_input",
        "merge_work",
    }
    for name in protected:
        description = described[name]
        for required in (
            "Generate client_operation_id before the first attempt",
            "complete immutable tool arguments",
            "every argument unchanged",
            "never invent a replacement",
            "new intent requires a new UUID",
            "historical original result",
        ):
            assert required in description, (name, required)


async def test_tool_catalog_schemas_and_annotations(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {
        "list_code_reviews", "get_code_review", "complete_code_review",
        "list_work_follow_ups", "get_work_follow_up", "respond_to_work_follow_up",
        "get_activity",
        "get_project_settings",
        "list_job_completion_reports",
        "get_job_completion_report",
        "list_projects",
        "create_project",
        "create_work",
        "search_work",
        "list_ready_work",
        "get_work",
        "add_checkpoint",
        "list_checkpoints",
        "list_completion_evidence",
        "recall_work",
        "append_event",
        "list_work_events",
        "claim_work",
        "claim_and_recall",
        "renew_claim",
        "release_claim",
        "add_relationship",
        "get_relationship",
        "list_relationships",
        "remove_relationship",
        "update_work",
        "complete_work",
        "delete_work",
        "request_human_input",
        "list_human_attention",
        "list_work_gates",
        "merge_work",
        "suggest_duplicate_work",
    }
    assert all(tool.outputSchema for tool in tools.values())
    assert all(
        tool.inputSchema.get("additionalProperties") is False for tool in tools.values()
    )
    for name in (
        "list_projects",
        "search_work",
        "get_work",
        "list_checkpoints",
        "list_completion_evidence",
        "list_ready_work",
        "recall_work",
        "get_relationship",
        "list_relationships",
        "list_work_events",
        "list_human_attention",
        "list_work_gates",
        "suggest_duplicate_work",
    ):
        assert tools[name].annotations.readOnlyHint is True
    for name in (
        "create_project",
        "create_work",
        "add_checkpoint",
        "claim_work",
        "claim_and_recall",
        "renew_claim",
        "release_claim",
        "add_relationship",
        "append_event",
        "request_human_input",
    ):
        assert tools[name].annotations.readOnlyHint is False
        assert tools[name].annotations.destructiveHint is False
    for name in (
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
        "merge_work",
    ):
        assert tools[name].annotations.destructiveHint is True
    protected = {
        "create_work",
        "add_checkpoint",
        "append_event",
        "add_relationship",
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
        "release_claim",
        "request_human_input",
        "merge_work",
    }
    protected |= {"respond_to_work_follow_up", "complete_code_review"}
    mutating = protected | {
        "create_project",
        "claim_work",
        "claim_and_recall",
        "renew_claim",
    }
    destructive = {
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
        "merge_work",
    }
    assert len(tools) == 38
    for name in mutating:
        assert tools[name].annotations.idempotentHint is (name in protected)
    for name in tools.keys() - mutating:
        assert tools[name].annotations.idempotentHint is True

    for name, tool in tools.items():
        annotations = tool.annotations
        assert (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        ) == (
            name not in mutating,
            name in destructive,
            name not in mutating or name in protected,
            False,
        )


async def test_tool_catalog_operation_and_claim_schemas(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}
    protected = {
        "respond_to_work_follow_up", "complete_code_review",
        "create_work",
        "add_checkpoint",
        "append_event",
        "add_relationship",
        "update_work",
        "complete_work",
        "delete_work",
        "remove_relationship",
        "release_claim",
        "request_human_input",
        "merge_work",
    }

    for name, tool in tools.items():
        properties = tool.inputSchema["properties"]
        if name in protected:
            assert "client_operation_id" in tool.inputSchema["required"]
            assert properties["client_operation_id"]["format"] == "uuid"
        else:
            assert "client_operation_id" not in properties
    assert tools["create_project"].outputSchema["additionalProperties"] is False
    project_page_schema = tools["list_projects"].outputSchema
    assert project_page_schema["additionalProperties"] is False
    assert project_page_schema["$defs"]["Project"]["additionalProperties"] is False

    for name in tools.keys() - {"list_projects", "create_project"}:
        assert "project_id" in tools[name].inputSchema["required"]

    claim_fields = {
        "project_id",
        "work_item_id",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
    }
    for name in ("claim_work", "claim_and_recall"):
        assert set(tools[name].inputSchema["required"]) == claim_fields
        properties = tools[name].inputSchema["properties"]
        assert properties["holder_client"]["maxLength"] == 80
        assert properties["holder_session_id"]["maxLength"] == 200
        assert properties["claim_request_id"]["maxLength"] == 200
        assert "lease_token" not in properties

    receipt_fields = {
        "purpose", "code_review_id", "mode", "lease_generation_id", "code_review_version",
        "scope_sha256",
        "work_item_id",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
        "acquired_at",
        "renewed_at",
        "expires_at",
        "lease_token",
    }
    for name in ("claim_work", "renew_claim"):
        assert set(tools[name].outputSchema["properties"]) == receipt_fields
        assert tools[name].outputSchema["additionalProperties"] is False
    assert set(tools["claim_and_recall"].outputSchema["properties"]) == {
        "lease",
        "context",
    }
    assert tools["claim_and_recall"].outputSchema["additionalProperties"] is False
    assert set(tools["release_claim"].outputSchema["properties"]) == {
        "work_item_id",
        "released",
    }
    assert tools["release_claim"].outputSchema["additionalProperties"] is False

    assert set(tools["renew_claim"].inputSchema["required"]) == {
        "project_id",
        "work_item_id",
        "lease_token",
    }
    assert set(tools["release_claim"].inputSchema["required"]) == {
        "project_id",
        "work_item_id",
        "lease_token",
        "actor_client",
        "actor_session_id",
        "client_operation_id",
    }
    for name in ("renew_claim", "release_claim"):
        token_schema = tools[name].inputSchema["properties"]["lease_token"]
        assert token_schema["minLength"] == 1
        assert token_schema["maxLength"] == 200
        assert token_schema["format"] == "password"
        assert token_schema["writeOnly"] is True


async def test_tool_catalog_mutation_and_relationship_schemas(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}

    for name in ("update_work", "delete_work", "release_claim", "remove_relationship"):
        required = set(tools[name].inputSchema["required"])
        assert {"actor_client", "actor_session_id"} <= required
        assert tools[name].inputSchema["properties"]["actor_model"]["default"] is None

    lease_capable_mutations = {
        "add_checkpoint",
        "update_work",
        "complete_work",
        "delete_work",
    }
    for name in lease_capable_mutations:
        assert "lease_token" in tools[name].inputSchema["properties"]
        assert "lease_token" not in tools[name].inputSchema["required"]

    create_required = tools["create_work"].inputSchema["required"]
    assert {
        "project_id",
        "title",
        "summary",
        "initial_checkpoint",
        "client_operation_id",
    } <= set(create_required)
    checkpoint_schema = tools["create_work"].inputSchema["$defs"]["CheckpointInput"]
    assert {"prompt", "source_client", "source_session_id"} <= set(
        checkpoint_schema["required"]
    )
    assert checkpoint_schema["additionalProperties"] is False
    assert {
        name
        for name, tool in tools.items()
        if "affected_paths" in json.dumps(tool.inputSchema, sort_keys=True)
    } == {"create_work", "add_checkpoint", "complete_work"}
    expected_scope_schema = {
        "items": {
            "maxLength": 512,
            "minLength": 1,
            "pattern": "^[A-Za-z0-9._@+=,~*/-]+$",
            "type": "string",
        },
        "maxItems": 64,
        "title": "Affected Paths",
        "type": "array",
    }
    for name in ("create_work", "add_checkpoint", "complete_work"):
        assert (
            tools[name].inputSchema["$defs"]["CheckpointInput"]["properties"][
                "affected_paths"
            ]
            == expected_scope_schema
        )

    relationship_types = {
        "blocks",
        "parent-child",
        "discovered-from",
        "duplicate-of",
        "related",
    }
    initial_relationships_schema = tools["create_work"].inputSchema["properties"][
        "initial_relationships"
    ]
    initial_relationships_array = next(
        option
        for option in initial_relationships_schema["anyOf"]
        if option.get("type") == "array"
    )
    assert initial_relationships_array["maxItems"] == 10
    initial_relationship_schema = tools["create_work"].inputSchema["$defs"][
        "InitialRelationshipInput"
    ]
    assert initial_relationship_schema["additionalProperties"] is False
    assert set(initial_relationship_schema["required"]) == {
        "type",
        "direction",
        "other_work_item_id",
    }
    assert set(initial_relationship_schema["properties"]) == {
        "type",
        "direction",
        "other_work_item_id",
        "context_checkpoint_id",
    }
    assert set(initial_relationship_schema["properties"]["type"]["enum"]) == (
        relationship_types
    )
    assert initial_relationship_schema["properties"]["direction"]["enum"] == [
        "incoming",
        "outgoing",
    ]

    add_relationship_schema = tools["add_relationship"].inputSchema
    assert set(add_relationship_schema["required"]) == {
        "project_id",
        "source_work_item_id",
        "target_work_item_id",
        "relationship_type",
        "created_by_client",
        "created_by_session_id",
        "client_operation_id",
    }
    assert set(add_relationship_schema["properties"]["relationship_type"]["enum"]) == (
        relationship_types
    )
    assert "context_checkpoint_work_item_id" not in add_relationship_schema["properties"]
    list_relationship_schema = tools["list_relationships"].inputSchema["properties"]
    assert list_relationship_schema["direction"]["default"] == "both"
    assert list_relationship_schema["direction"]["enum"] == [
        "incoming",
        "outgoing",
        "undirected",
        "both",
    ]
    assert list_relationship_schema["limit"]["default"] == 50
    assert list_relationship_schema["limit"]["maximum"] == 100
    counterpart_schema = json.dumps(tools["list_relationships"].outputSchema)
    assert '"prompt"' not in counterpart_schema
    assert '"source_metadata"' not in counterpart_schema
    assert '"lease_token"' not in counterpart_schema
    assert set(tools["remove_relationship"].outputSchema["properties"]) == {
        "project_id",
        "relationship_id",
        "removed",
    }
    search_work_schema = json.dumps(tools["search_work"].outputSchema)
    assert '"prompt"' not in search_work_schema
    assert '"source_metadata"' not in search_work_schema
    assert '"source_session_url"' not in search_work_schema
    assert tools["search_work"].inputSchema["properties"]["semantic"]["default"] is False
    assert tools["search_work"].inputSchema["properties"]["status"]["enum"] == [
        "pending",
        "active",
        "dropped",
        "deferred",
        "done",
        "wont-do",
        "promoted",
        "all",
    ]


async def test_tool_catalog_ready_event_and_gate_schemas(settings):
    server = build_server(settings)
    tools = {tool.name: tool for tool in await server.list_tools()}

    ready_input = tools["list_ready_work"].inputSchema
    assert set(ready_input["properties"]) == {
        "project_id",
        "min_priority",
        "tag",
        "parent_work_item_id",
        "limit",
        "offset",
    }
    assert ready_input["properties"]["min_priority"]["default"] == 0
    assert ready_input["properties"]["limit"]["default"] == 30
    ready_output = tools["list_ready_work"].outputSchema
    assert ready_output["additionalProperties"] is False
    assert ready_output["$defs"]["WorkSummaryMinimal"]["additionalProperties"] is False
    ready_schema = json.dumps(ready_output)
    for forbidden in ("prompt", "source_metadata", "source_session_url", "summary"):
        assert f'"{forbidden}"' not in ready_schema

    append_input = tools["append_event"].inputSchema
    assert set(append_input["properties"]) == {
        "project_id",
        "work_item_id",
        "body",
        "metadata",
        "actor_client",
        "actor_session_id",
        "actor_model",
        "client_operation_id",
    }
    assert set(append_input["required"]) == {
        "project_id",
        "work_item_id",
        "body",
        "actor_client",
        "actor_session_id",
        "client_operation_id",
    }
    assert append_input["properties"]["metadata"]["default"] == {}
    assert "event_type" not in append_input["properties"]
    assert "lease_token" not in append_input["properties"]
    event_list_input = tools["list_work_events"].inputSchema["properties"]
    assert event_list_input["order"]["default"] == "oldest"
    assert event_list_input["limit"]["default"] == 50
    assert event_list_input["limit"]["maximum"] == 100
    event_output = tools["list_work_events"].outputSchema
    assert event_output["additionalProperties"] is False
    assert set(event_output["properties"]) == {
        "items",
        "total",
        "limit",
        "offset",
        "pre_phase5_history_may_be_incomplete",
    }
    assert event_output["$defs"]["WorkEventRead"]["additionalProperties"] is False
    context_output = tools["recall_work"].outputSchema["properties"]
    assert {
        "recent_events",
        "event_total",
        "omitted_event_count",
        "pre_phase5_history_may_be_incomplete",
    } <= set(context_output)

    request_gate_input = tools["request_human_input"].inputSchema
    assert set(request_gate_input["required"]) == {
        "project_id",
        "work_item_id",
        "question",
        "requested_by_client",
        "requested_by_session_id",
        "client_operation_id",
    }
    assert set(request_gate_input["properties"]) == {
        "project_id",
        "work_item_id",
        "question",
        "requested_by_client",
        "requested_by_session_id",
        "requested_by_model",
        "client_operation_id",
    }
    assert request_gate_input["properties"]["question"]["maxLength"] == 4000
    assert request_gate_input["properties"]["requested_by_client"]["maxLength"] == 80
    assert request_gate_input["properties"]["requested_by_session_id"]["maxLength"] == 200
    assert "gate_type" not in request_gate_input["properties"]
    assert "lease_token" not in request_gate_input["properties"]
    request_gate_output = tools["request_human_input"].outputSchema
    assert request_gate_output["additionalProperties"] is False
    assert set(request_gate_output["properties"]) == {
        "id",
        "project_id",
        "work_item_id",
        "gate_type",
        "question",
        "requested_by_client",
        "requested_by_session_id",
        "requested_by_model",
        "requested_context_revision",
        "created_at",
        "status",
        "current_context_revision",
        "work_changed_since_request",
        "context_checkpoint_changed_since_request",
        "relationships_changed_since_request",
        "context_changed_since_request",
        "resolved_at",
        "resolution",
        "resolved_by_client",
        "resolved_by_session_id",
        "resolved_by_model",
        "resolved_context_revision",
        "context_changed_at_resolution",
    }

    attention_input = tools["list_human_attention"].inputSchema
    assert set(attention_input["properties"]) == {
        "project_id",
        "work_item_id",
        "limit",
        "cursor",
    }
    assert attention_input["properties"]["limit"]["default"] == 30
    assert attention_input["properties"]["limit"]["minimum"] == 0
    assert attention_input["properties"]["limit"]["maximum"] == 100
    attention_cursor_schema = next(
        option
        for option in attention_input["properties"]["cursor"]["anyOf"]
        if option.get("type") == "string"
    )
    assert attention_cursor_schema["maxLength"] == 4096
    history_input = tools["list_work_gates"].inputSchema
    assert set(history_input["properties"]) == {
        "project_id",
        "work_item_id",
        "status",
        "limit",
        "cursor",
    }
    assert history_input["properties"]["status"]["default"] == "all"
    assert history_input["properties"]["status"]["enum"] == [
        "all",
        "unresolved",
        "resolved",
    ]
    assert history_input["properties"]["limit"]["minimum"] == 1
    assert history_input["properties"]["limit"]["maximum"] == 100
    assert tools["list_human_attention"].outputSchema["additionalProperties"] is False
    assert tools["list_work_gates"].outputSchema["additionalProperties"] is False
    assert "resolve_human_input" not in tools

    context_output = tools["recall_work"].outputSchema["properties"]
    assert {
        "unresolved_gates",
        "unresolved_gate_total",
        "omitted_unresolved_gate_count",
        "recent_resolved_gates",
        "resolved_gate_total",
        "omitted_resolved_gate_count",
    } <= set(context_output)
    assert "waiting" in json.dumps(tools["search_work"].outputSchema)
    assert "human_attention_requested" in json.dumps(
        tools["list_work_events"].outputSchema
    )
    assert "human_attention_resolved" in json.dumps(
        tools["list_work_events"].outputSchema
    )

    work_changes_schema = tools["update_work"].inputSchema["$defs"]["WorkChanges"]
    assert work_changes_schema["additionalProperties"] is False
    assert "prompt" not in work_changes_schema["properties"]
    assert "source_session_id" not in work_changes_schema["properties"]
    for name, tool in tools.items():
        if name not in {"claim_work", "claim_and_recall", "renew_claim"}:
            assert '"lease_token"' not in json.dumps(tool.outputSchema)


async def test_all_protected_mutations_forward_one_canonical_top_level_uuid(settings):
    calls = []

    class CapturingAPI:
        async def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            raise ToolError("captured protected request")

    server = build_server(settings, CapturingAPI())
    uppercase_id = CLIENT_OPERATION_ID.upper()
    arguments_by_tool = protected_tool_arguments(uppercase_id)

    for expected_count, (tool_name, arguments) in enumerate(
        arguments_by_tool.items(), start=1
    ):
        with pytest.raises(ToolError, match="captured protected request"):
            await server.call_tool(tool_name, arguments)

        assert len(calls) == expected_count
        method, path, kwargs = calls[-1]
        assert method in {"POST", "PATCH", "DELETE"}
        assert path.startswith(f"projects/{PROJECT_ID}/")
        assert kwargs["effect"] == TransportEffect.RECEIPT_PROTECTED_WRITE
        payload = kwargs["payload"]
        assert payload["client_operation_id"] == CLIENT_OPERATION_ID
        assert json.dumps(payload).count('"client_operation_id"') == 1


async def test_all_protected_mutations_reject_missing_or_invalid_uuid_locally(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    server = adapter(settings, handler)
    for tool_name, valid_arguments in protected_tool_arguments().items():
        missing = {
            name: value
            for name, value in valid_arguments.items()
            if name != "client_operation_id"
        }
        with pytest.raises(ToolError, match=r"client_operation_id \(missing\)"):
            await server.call_tool(tool_name, missing)

        invalid_marker = f"private-invalid-operation-id-{tool_name}"
        with pytest.raises(
            ToolError, match=r"client_operation_id \(uuid_parsing\)"
        ) as caught:
            await server.call_tool(
                tool_name,
                {**valid_arguments, "client_operation_id": invalid_marker},
            )
        assert invalid_marker not in str(caught.value)

    assert calls == []


async def test_excluded_mutations_reject_unexpected_operation_id_locally(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    excluded = {
        "create_project": {"name": "Example"},
        "claim_work": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "holder_client": "claude-code",
            "holder_session_id": "phase-6-session",
            "claim_request_id": CLAIM_REQUEST_ID,
        },
        "claim_and_recall": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "holder_client": "claude-code",
            "holder_session_id": "phase-6-session",
            "claim_request_id": CLAIM_REQUEST_ID,
        },
        "renew_claim": {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "lease_token": LEASE_TOKEN,
        },
    }
    server = adapter(settings, handler)
    for tool_name, arguments in excluded.items():
        with pytest.raises(
            ToolError, match=r"client_operation_id \(extra_forbidden\)"
        ):
            await server.call_tool(
                tool_name,
                {**arguments, "client_operation_id": CLIENT_OPERATION_ID},
            )

    assert calls == []


async def test_strict_tool_models_are_isolated_to_mnemonic(settings):
    await build_server(settings).list_tools()

    vanilla = FastMCP("Vanilla")

    @vanilla.tool()
    async def echo(value: str) -> str:
        return value

    [tool] = await vanilla.list_tools()
    assert "additionalProperties" not in tool.inputSchema
    result = structured(
        await vanilla.call_tool("echo", {"value": "ok", "extra": "ignored"})
    )
    assert result == {"result": "ok"}


async def test_projects_http_boundary_and_pagination(settings, project):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        assert request.url.path == "/api/v1/projects"
        if request.method == "GET":
            assert dict(request.url.params) == {"limit": "4", "offset": "8"}
            return httpx.Response(200, json={"items": [project], "total": 9, "limit": 4, "offset": 8})
        assert json.loads(request.content)["name"] == "Example"
        return httpx.Response(201, json=project)

    server = adapter(settings, handler)
    result = structured(await server.call_tool("list_projects", {"limit": 4, "offset": 8}))
    assert result["items"][0]["id"] == PROJECT_ID
    assert result["total"] == 9
    created = structured(await server.call_tool("create_project", {"name": "Example"}))
    assert created["slug"] == "example"
    assert len(requests) == 2


async def test_project_tools_reject_unplanned_upstream_fields(settings, project):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [{**project, "private_upstream_field": "must not be ignored"}],
                "total": 1,
                "limit": 100,
                "offset": 0,
            },
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool("list_projects", {})


async def test_create_work_preserves_nested_checkpoint_and_returns_both_records(
    settings, work_item, checkpoint
):
    prompt = "  Historical context.\r\n\nCode: `x = 1`\nUnicode: β / 🔍\n  "
    metadata = {"evidence": [{"path": "src/search.py", "verified": False}], "count": 2}
    checkpoint_input = {
        "prompt": prompt,
        "source_client": "claude-code",
        "source_session_id": checkpoint["source_session_id"],
        "source_model": "test-model",
        "source_session_url": "https://example.invalid/session/opaque",
        "repository_branch": "main",
        "verified_against": "abcdef1",
        "affected_paths": ["src/search.py", "tests/**"],
        "tags": ["search"],
        "source_metadata": metadata,
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert request.extensions["timeout"]["read"] == 20.0
        assert json.loads(request.content) == {
            **OPERATION_PAYLOAD,
            "title": work_item["title"],
            "summary": work_item["summary"],
            "priority": 7,
            "status": "pending",
            "initial_checkpoint": checkpoint_input,
        }
        return httpx.Response(
            201,
            json={
                "work_item": {**work_item, "version": 1},
                "initial_checkpoint": {**checkpoint, **checkpoint_input},
                "initial_relationships": [],
            },
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "create_work",
            {
                "project_id": PROJECT_ID,
                **OPERATION_ARGUMENT,
                "title": work_item["title"],
                "summary": work_item["summary"],
                "priority": 7,
                "initial_checkpoint": checkpoint_input,
            },
        )
    )
    assert result["work_item"]["id"] == WORK_ID
    assert result["initial_checkpoint"]["prompt"] == prompt
    assert result["initial_checkpoint"]["affected_paths"] == [
        "src/search.py",
        "tests/**",
    ]
    assert result["initial_checkpoint"]["source_metadata"] == metadata
    assert result["initial_relationships"] == []


async def test_create_work_serializes_atomic_initial_relationships(
    settings, work_item, checkpoint
):
    checkpoint_input = {
        name: checkpoint[name]
        for name in (
            "prompt",
            "source_client",
            "source_session_id",
            "source_model",
            "source_session_url",
            "repository_branch",
            "verified_against",
            "tags",
            "source_metadata",
        )
    }
    initial_relationship = {
        "type": "discovered-from",
        "direction": "outgoing",
        "other_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
    }
    relationship = {
        "id": RELATIONSHIP_ID,
        "project_id": PROJECT_ID,
        "relationship_type": "discovered-from",
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
        "created_by_client": checkpoint["source_client"],
        "created_by_session_id": checkpoint["source_session_id"],
        "created_by_model": checkpoint["source_model"],
        "created_at": checkpoint["created_at"],
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert json.loads(request.content) == {
            **OPERATION_PAYLOAD,
            "title": work_item["title"],
            "summary": work_item["summary"],
            "priority": 0,
            "status": "pending",
            "initial_checkpoint": checkpoint_input,
            "initial_relationships": [initial_relationship],
        }
        return httpx.Response(
            201,
            json={
                "work_item": {**work_item, "priority": 0, "version": 1},
                "initial_checkpoint": checkpoint,
                "initial_relationships": [relationship],
            },
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "create_work",
            {
                "project_id": PROJECT_ID,
                **OPERATION_ARGUMENT,
                "title": work_item["title"],
                "summary": work_item["summary"],
                "initial_checkpoint": checkpoint_input,
                "initial_relationships": [initial_relationship],
            },
        )
    )
    assert result["initial_relationships"] == [relationship]


async def test_relationship_tools_use_exact_rest_contract_and_pointer_only_counterparts(
    settings, relationship, adjacent_relationship, work_context
):
    upstream_adjacency = {
        **adjacent_relationship,
        "relationship": {
            **adjacent_relationship["relationship"],
            "project_id": OTHER_CHECKPOINT_ID,
        },
        "counterpart": {
            **adjacent_relationship["counterpart"],
            "project_id": OTHER_CHECKPOINT_ID,
            "prompt": "must not cross the pointer boundary",
            "summary": "must not cross the pointer boundary",
            "source_metadata": {"private": True},
            "source_session_url": "https://example.invalid/private",
        },
    }
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        relationship_path = f"/api/v1/projects/{PROJECT_ID}/relationships"
        if request.method == "POST":
            assert request.url.path == relationship_path
            assert not request.url.params
            assert json.loads(request.content) == {
                **OPERATION_PAYLOAD,
                "source_work_item_id": OTHER_WORK_ID,
                "target_work_item_id": WORK_ID,
                "relationship_type": "blocks",
                "created_by_client": relationship["created_by_client"],
                "created_by_session_id": relationship["created_by_session_id"],
                "created_by_model": relationship["created_by_model"],
                "context_checkpoint_id": None,
            }
            return httpx.Response(
                200, json={"relationship": relationship, "created": False}
            )
        if request.url.path.endswith(f"/work-items/{WORK_ID}/relationships"):
            assert request.method == "GET"
            assert dict(request.url.params) == {
                "direction": "incoming",
                "limit": "7",
                "offset": "2",
                "type": "blocks",
            }
            return httpx.Response(
                200,
                json={"items": [upstream_adjacency], "total": 3, "limit": 7, "offset": 2},
            )
        if request.url.path.endswith(f"/work-items/{WORK_ID}/context"):
            assert request.method == "GET"
            assert dict(request.url.params) == {
                "recent_limit": "5",
                "recent_event_limit": "10",
            }
            return httpx.Response(
                200,
                json={
                    **work_context,
                    "incoming_relationships": [upstream_adjacency],
                    "relationship_counts": {
                        "incoming": 1,
                        "outgoing": 0,
                        "undirected": 0,
                        "total": 1,
                    },
                    "duplicate_merge_eligibility": {
                        **work_context["duplicate_merge_eligibility"],
                        "incident_blocks_count": 1,
                    },
                },
            )
        assert request.url.path == f"{relationship_path}/{RELATIONSHIP_ID}"
        if request.method == "GET":
            return httpx.Response(200, json=relationship)
        assert request.method == "DELETE"
        assert json.loads(request.content) == {**OPERATION_PAYLOAD, **ACTOR_PAYLOAD}
        return httpx.Response(
            200,
            json={
                "project_id": PROJECT_ID,
                "relationship_id": RELATIONSHIP_ID,
                "removed": True,
            },
        )

    server = adapter(settings, handler)
    created = structured(
        await server.call_tool(
            "add_relationship",
            {
                "project_id": PROJECT_ID,
                **OPERATION_ARGUMENT,
                "source_work_item_id": OTHER_WORK_ID,
                "target_work_item_id": WORK_ID,
                "relationship_type": "blocks",
                "created_by_client": relationship["created_by_client"],
                "created_by_session_id": relationship["created_by_session_id"],
                "created_by_model": relationship["created_by_model"],
            },
        )
    )
    assert created == {"relationship": relationship, "created": False}
    fetched = structured(
        await server.call_tool(
            "get_relationship",
            {"project_id": PROJECT_ID, "relationship_id": RELATIONSHIP_ID},
        )
    )
    assert fetched == relationship
    listed = structured(
        await server.call_tool(
            "list_relationships",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "direction": "incoming",
                "relationship_type": "blocks",
                "limit": 7,
                "offset": 2,
            },
        )
    )
    assert listed["total"] == 3
    counterpart = listed["items"][0]["counterpart"]
    assert counterpart["id"] == OTHER_WORK_ID
    assert counterpart["project_id"] == OTHER_CHECKPOINT_ID
    assert set(counterpart) == {"project_id", "id", "title", "status", "readiness"}
    recalled = structured(
        await server.call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    )
    assert recalled["relationship_counts"]["incoming"] == 1
    assert set(recalled["incoming_relationships"][0]["counterpart"]) == {
        "project_id",
        "id",
        "title",
        "status",
        "readiness",
    }
    removed = structured(
        await server.call_tool(
            "remove_relationship",
            {
                "project_id": PROJECT_ID,
                "relationship_id": RELATIONSHIP_ID,
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert removed == {
        "project_id": PROJECT_ID,
        "relationship_id": RELATIONSHIP_ID,
        "removed": True,
    }
    assert seen == [
        ("POST", f"/api/v1/projects/{PROJECT_ID}/relationships"),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/relationships/{RELATIONSHIP_ID}"),
        (
            "GET",
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/relationships",
        ),
        ("GET", f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"),
        ("DELETE", f"/api/v1/projects/{PROJECT_ID}/relationships/{RELATIONSHIP_ID}"),
    ]


async def test_search_work_is_open_only_and_pointer_only(settings, work_summary):
    upstream_summary = {
        **work_summary,
        "current_context": {
            **work_summary["current_context"],
            "prompt": "must not cross the search boundary",
            "source_metadata": {"private": True},
            "source_session_url": "https://example.invalid/private",
        },
    }

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert dict(request.url.params) == {
            "q": "src/search.py",
            "status": "pending",
            "view": "full",
            "duplicate_scope": "canonical",
            "limit": "30",
            "offset": "0",
        }
        assert request.extensions["timeout"]["read"] == 20.0
        # The adapter must still enforce a compact output if an API regression
        # accidentally adds full content to this response.
        return httpx.Response(
            200,
            json={
                "items": [{
                    "summary": upstream_summary,
                    "matched_member": {
                        "id": WORK_ID,
                        "title": work_summary["work_item"]["title"],
                        "status": "pending",
                    },
                }],
                "total": 1,
                "limit": 30,
                "offset": 0,
            },
        )

    result = structured(
        await adapter(settings, handler).call_tool(
            "search_work", {"project_id": PROJECT_ID, "q": "src/search.py"}
        )
    )
    pointer = result["items"][0]["summary"]["current_context"]
    assert pointer["id"] == CHECKPOINT_ID
    assert "prompt" not in pointer
    assert "source_metadata" not in pointer
    assert "source_session_url" not in pointer



async def test_ready_work_uses_exact_query_and_strict_pointer_envelope(
    settings, work_item
):
    minimal = {
        "work_item": {
            name: work_item[name]
            for name in ("id", "title", "status", "priority", "version", "updated_at")
        },
        "checkpoint_count": 3,
        "display_state": "pending",
    }

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/ready-work"
        assert dict(request.url.params) == {
            "min_priority": "5",
            "tag": "Search",
            "parent_work_item_id": OTHER_WORK_ID,
            "limit": "2",
            "offset": "3",
        }
        return httpx.Response(
            200,
            json={"items": [minimal], "total": 8, "limit": 2, "offset": 3},
        )

    ready = structured(
        await adapter(settings, handler).call_tool(
            "list_ready_work",
            {
                "project_id": PROJECT_ID,
                "min_priority": 5,
                "tag": "Search",
                "parent_work_item_id": OTHER_WORK_ID,
                "limit": 2,
                "offset": 3,
            },
        )
    )
    assert ready == {"items": [minimal], "total": 8, "limit": 2, "offset": 3}
    assert set(ready["items"][0]) == {"work_item", "checkpoint_count", "display_state"}
    assert "summary" not in ready["items"][0]["work_item"]


async def test_ready_work_rejects_an_accidental_full_upstream_projection(
    settings, work_item
):
    minimal = {
        "work_item": {
            name: work_item[name]
            for name in ("id", "title", "status", "priority", "version", "updated_at")
        },
        "checkpoint_count": 1,
        "display_state": "pending",
        "current_context": {"prompt": "must not cross the ready pointer boundary"},
    }

    def handler(request):
        return httpx.Response(
            200,
            json={"items": [minimal], "total": 1, "limit": 30, "offset": 0},
        )

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, handler).call_tool(
            "list_ready_work", {"project_id": PROJECT_ID}
        )
    assert "must not cross" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "display_state"),
    [("done", "done"), ("pending", "blocked"), ("pending", "waiting")],
)
async def test_ready_work_rejects_non_ready_upstream_items(
    settings, work_item, status, display_state
):
    minimal = {
        "work_item": {
            **{
                name: work_item[name]
                for name in ("id", "title", "priority", "version", "updated_at")
            },
            "status": status,
        },
        "checkpoint_count": 1,
        "display_state": display_state,
    }

    def handler(request):
        return httpx.Response(
            200,
            json={"items": [minimal], "total": 1, "limit": 30, "offset": 0},
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool(
            "list_ready_work", {"project_id": PROJECT_ID}
        )


@pytest.mark.parametrize(
    "invalid_page",
    [
        {"items": [], "total": -1, "limit": 30, "offset": 0},
        {"items": [], "total": 0, "limit": 0, "offset": 0},
        {"items": [], "total": 0, "limit": 101, "offset": 0},
        {"items": [], "total": 0, "limit": 30, "offset": -1},
        {"items": [None, None], "total": 2, "limit": 1, "offset": 0},
        {"items": [None], "total": 1, "limit": 30, "offset": 1},
        {"items": [None], "total": 1, "limit": 30, "offset": 2},
        {"items": [None, None], "total": 2, "limit": 2, "offset": 1},
    ],
)
def test_ready_work_page_rejects_invalid_envelopes(work_item, invalid_page):
    minimal = {
        "work_item": {
            name: work_item[name]
            for name in ("id", "title", "status", "priority", "version", "updated_at")
        },
        "checkpoint_count": 1,
        "display_state": "pending",
    }
    payload = {
        **invalid_page,
        "items": [minimal if item is None else item for item in invalid_page["items"]],
    }

    with pytest.raises(ValueError):
        ReadyWorkPage.model_validate(payload)


def test_ready_work_page_allows_empty_page_beyond_total():
    page = ReadyWorkPage.model_validate(
        {"items": [], "total": 1, "limit": 30, "offset": 500}
    )

    assert page.total == 1
    assert page.offset == 500


async def test_request_human_input_forwards_one_exact_protected_intent(
    settings, human_gate
):
    question = "  Which rollout policy should this work use?\n"
    response = {**human_gate, "question": question}
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/gates"
        )
        assert not request.url.params
        assert json.loads(request.content) == {
            "client_operation_id": CLIENT_OPERATION_ID,
            "gate_type": "human",
            "question": question,
            "requested_by_client": "claude-code",
            "requested_by_session_id": "phase-7-session",
            "requested_by_model": "test-model",
        }
        return httpx.Response(201, json=response)

    result = structured(
        await adapter(settings, handler).call_tool(
            "request_human_input",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "question": question,
                "requested_by_client": "claude-code",
                "requested_by_session_id": "phase-7-session",
                "requested_by_model": "test-model",
                "client_operation_id": CLIENT_OPERATION_ID,
            },
        )
    )
    assert result == response
    assert len(requests) == 1


@pytest.mark.parametrize("outcome", ["network", "backend", "malformed_success"])
async def test_gate_request_unknown_outcomes_are_one_attempt_and_value_free(
    settings, human_gate, outcome
):
    requests = []
    private_marker = "private-gate-transport-detail"

    def handler(request):
        requests.append(request)
        if outcome == "network":
            raise httpx.ReadTimeout(private_marker, request=request)
        if outcome == "backend":
            return httpx.Response(500, json={"private": private_marker})
        return httpx.Response(201, json={"private": private_marker})

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "request_human_input", protected_tool_arguments()["request_human_input"]
        )
    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert "same tool" in message
    assert "every argument unchanged" in message
    assert private_marker not in message
    assert human_gate["question"] not in message
    assert CLIENT_OPERATION_ID not in message
    assert len(requests) == 1



@pytest.mark.parametrize("response_kind", ["drifted", "resolved"])
async def test_gate_request_rejects_model_valid_anchor_or_status_mismatch(
    settings, human_gate, resolved_human_gate, response_kind
):
    response = json.loads(json.dumps(human_gate))
    if response_kind == "drifted":
        response["current_context_revision"]["work_version"] = 4
        response["work_changed_since_request"] = True
        response["context_changed_since_request"] = True
    else:
        response = resolved_human_gate
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "request_human_input", protected_tool_arguments()["request_human_input"]
        )
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(caught.value)
    assert human_gate["question"] not in str(caught.value)
    assert len(calls) == 1


async def test_gate_reads_use_exact_cursor_queries_and_enforce_scope(
    settings, work_summary, human_gate, resolved_human_gate
):
    gated_summary = {
        **work_summary,
        "readiness": {
            **work_summary["readiness"],
            "unresolved_gate_count": 1,
            "is_gated": True,
            "is_ready": False,
            "display_state": "waiting",
        },
    }
    historical_resolved_gate = {
        **resolved_human_gate,
        "project_id": OTHER_CHECKPOINT_ID,
    }
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/human-attention"):
            return httpx.Response(
                200,
                json={
                    "items": [{"gate": human_gate, "summary": gated_summary}],
                    "total": 1,
                    "limit": 12,
                    "next_cursor": "attention-cursor-v1",
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [historical_resolved_gate],
                "total": 1,
                "limit": 7,
                "next_cursor": "history-cursor-v1",
            },
        )

    server = adapter(settings, handler)
    attention = structured(
        await server.call_tool(
            "list_human_attention",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "limit": 12,
                "cursor": "attention-start-v1",
            },
        )
    )
    history = structured(
        await server.call_tool(
            "list_work_gates",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "status": "resolved",
                "limit": 7,
                "cursor": "history-start-v1",
            },
        )
    )
    assert attention["items"][0]["gate"]["status"] == "unresolved"
    assert attention["items"][0]["summary"]["readiness"]["is_gated"] is True
    assert history["items"][0]["resolution"] == resolved_human_gate["resolution"]
    assert history["items"][0]["project_id"] == OTHER_CHECKPOINT_ID
    assert calls == [
        (
            "GET",
            f"/api/v1/projects/{PROJECT_ID}/human-attention",
            {
                "limit": "12",
                "work_item_id": WORK_ID,
                "cursor": "attention-start-v1",
            },
        ),
        (
            "GET",
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/gates",
            {
                "status": "resolved",
                "limit": "7",
                "cursor": "history-start-v1",
            },
        ),
    ]


async def test_gate_read_scope_or_filter_mismatch_is_rejected_without_values(
    settings, work_summary, human_gate
):
    gated_summary = {
        **work_summary,
        "readiness": {
            **work_summary["readiness"],
            "unresolved_gate_count": 1,
            "is_gated": True,
            "is_ready": False,
            "display_state": "waiting",
        },
    }
    other_gate = {
        **human_gate,
        "project_id": OTHER_WORK_ID,
        "work_item_id": OTHER_WORK_ID,
    }
    other_summary = {
        **gated_summary,
        "work_item": {
            **gated_summary["work_item"],
            "project_id": OTHER_WORK_ID,
            "id": OTHER_WORK_ID,
        },
        "current_context": {
            **gated_summary["current_context"],
            "work_item_id": OTHER_WORK_ID,
        },
        "readiness": {
            **gated_summary["readiness"],
            "canonical_work_item_id": OTHER_WORK_ID,
        },
    }

    def attention_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [{"gate": other_gate, "summary": other_summary}],
                "total": 1,
                "limit": 30,
                "next_cursor": None,
            },
        )

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, attention_handler).call_tool(
            "list_human_attention", {"project_id": PROJECT_ID}
        )
    assert human_gate["question"] not in str(caught.value)
    assert OTHER_WORK_ID not in str(caught.value)

    def history_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [other_gate],
                "total": 1,
                "limit": 30,
                "next_cursor": None,
            },
        )

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, history_handler).call_tool(
            "list_work_gates",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
            },
        )
    assert human_gate["question"] not in str(caught.value)
    assert GATE_ID not in str(caught.value)

    unresolved_historical_gate = {
        **human_gate,
        "project_id": OTHER_CHECKPOINT_ID,
    }

    def unresolved_history_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [unresolved_historical_gate],
                "total": 1,
                "limit": 30,
                "next_cursor": None,
            },
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, unresolved_history_handler).call_tool(
            "list_work_gates",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
            },
        )


async def test_attention_count_mode_is_text_free_and_rejects_a_cursor(
    settings, human_gate
):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"items": [], "total": 4, "limit": 0, "next_cursor": None},
        )

    server = adapter(settings, handler)
    count = structured(
        await server.call_tool(
            "list_human_attention", {"project_id": PROJECT_ID, "limit": 0}
        )
    )
    assert count == {"items": [], "total": 4, "limit": 0, "next_cursor": None}
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {"limit": "0"}
    assert human_gate["question"] not in calls[0].url.query.decode()

    with pytest.raises(ToolError, match=r"cursor \(value_error\)"):
        await server.call_tool(
            "list_human_attention",
            {
                "project_id": PROJECT_ID,
                "limit": 0,
                "cursor": "not-allowed-for-count",
            },
        )
    assert len(calls) == 1


def test_human_gate_models_enforce_types_and_resolution_nullability(
    human_gate, resolved_human_gate
):
    assert HumanGateRead.model_validate(human_gate).status == "unresolved"
    assert HumanGateRead.model_validate(resolved_human_gate).status == "resolved"

    invalid_revision = json.loads(json.dumps(human_gate))
    invalid_revision["requested_context_revision"]["work_version"] = "3"
    with pytest.raises(ValueError):
        HumanGateRead.model_validate(invalid_revision)
    with pytest.raises(ValueError):
        HumanGateRead.model_validate(
            {**human_gate, "work_changed_since_request": "false"}
        )
    backend_computed_drift = {**human_gate, "work_changed_since_request": True}
    assert HumanGateRead.model_validate(backend_computed_drift).work_changed_since_request is True
    with pytest.raises(ValueError):
        HumanGateRead.model_validate(
            {**human_gate, "resolution": "An impossible unresolved answer."}
        )



def test_gate_event_models_preserve_legacy_wire_and_validate_typed_metadata(
    progress_event, human_gate, resolved_human_gate
):
    base = {
        **progress_event,
        "event_type": "human_attention_requested",
        "body": human_gate["question"],
        "metadata": {"gate_id": GATE_ID, "gate_type": "human"},
    }
    requested = WorkEventRead.model_validate(base)
    resolved = WorkEventRead.model_validate(
        {
            **base,
            "event_type": "human_attention_resolved",
            "body": resolved_human_gate["resolution"],
        }
    )
    serialized = requested.model_dump(mode="json")
    assert str(requested.metadata.gate_id) == GATE_ID
    assert resolved.event_type == "human_attention_resolved"
    assert "gate_id" not in serialized
    assert serialized["metadata"] == {"gate_id": GATE_ID, "gate_type": "human"}
    with pytest.raises(ValueError):
        WorkEventRead.model_validate({**base, "origin": "backfill"})
    with pytest.raises(ValueError):
        WorkEventRead.model_validate({**base, "gate_id": GATE_ID})


def test_move_event_models_accept_client_and_unattributed_witnesses(progress_event):
    metadata = {
        "move_id": RELATIONSHIP_ID,
        "source_project_id": PROJECT_ID,
        "target_project_id": OTHER_WORK_ID,
        "role": "source",
        "work_version": 4,
    }
    source = WorkEventRead.model_validate(
        {
            **progress_event,
            "event_type": "work_moved",
            "body": None,
            "metadata": metadata,
        }
    )
    target = WorkEventRead.model_validate(
        {
            **progress_event,
            "project_id": OTHER_WORK_ID,
            "event_type": "work_moved",
            "actor_kind": "unattributed",
            "actor_client": None,
            "actor_session_id": None,
            "actor_model": None,
            "body": None,
            "metadata": {**metadata, "role": "target"},
        }
    )
    assert source.metadata.model_dump(mode="json") == metadata
    assert target.actor_kind == "unattributed"

    with pytest.raises(ValueError, match="role does not match its project"):
        WorkEventRead.model_validate(
            {
                **progress_event,
                "event_type": "work_moved",
                "body": None,
                "metadata": {**metadata, "role": "target"},
            }
        )
    with pytest.raises(ValueError, match="projects must be distinct"):
        WorkEventRead.model_validate(
            {
                **progress_event,
                "event_type": "work_moved",
                "body": None,
                "metadata": {**metadata, "target_project_id": PROJECT_ID},
            }
        )


async def test_event_tools_use_exact_rest_contract(settings, progress_event):
    seen: list[str] = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/events"
        )
        if request.method == "POST":
            assert not request.url.params
            assert json.loads(request.content) == {
                **OPERATION_PAYLOAD,
                "event_type": "progress",
                "body": progress_event["body"],
                "metadata": progress_event["metadata"],
                **ACTOR_PAYLOAD,
            }
            return httpx.Response(201, json=progress_event)
        assert dict(request.url.params) == {
            "order": "newest",
            "event_type": "progress",
            "limit": "7",
            "offset": "2",
        }
        return httpx.Response(
            200,
            json={
                "items": [progress_event],
                "total": 11,
                "limit": 7,
                "offset": 2,
                "pre_phase5_history_may_be_incomplete": True,
            },
        )

    server = adapter(settings, handler)
    appended = structured(
        await server.call_tool(
            "append_event",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "body": progress_event["body"],
                "metadata": progress_event["metadata"],
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert appended == progress_event
    listed = structured(
        await server.call_tool(
            "list_work_events",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "order": "newest",
                "event_type": "progress",
                "limit": 7,
                "offset": 2,
            },
        )
    )
    assert listed["items"] == [progress_event]
    assert listed["pre_phase5_history_may_be_incomplete"] is True
    assert seen == ["POST", "GET"]


async def test_historical_progress_operation_key_remains_readable(
    settings, progress_event, work_context
):
    historical_metadata = {
        "outer": [{"Client_Operation_ID": "historically-legal"}]
    }
    historical_event = {**progress_event, "metadata": historical_metadata}

    def handler(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "items": [historical_event],
                    "total": 1,
                    "limit": 50,
                    "offset": 0,
                    "pre_phase5_history_may_be_incomplete": False,
                },
            )
        return httpx.Response(
            200,
            json={
                **work_context,
                "recent_events": [historical_event],
                "event_total": 1,
                "omitted_event_count": 0,
            },
        )

    server = adapter(settings, handler)
    listed = structured(
        await server.call_tool(
            "list_work_events",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )
    recalled = structured(
        await server.call_tool(
            "recall_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )

    assert listed["items"][0]["metadata"] == historical_metadata
    assert recalled["recent_events"][0]["metadata"] == historical_metadata


async def test_work_updated_event_allows_only_same_value_status_metadata(
    settings, same_status_update_event
):
    def valid_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [same_status_update_event],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "pre_phase5_history_may_be_incomplete": False,
            },
        )

    listed = structured(
        await adapter(settings, valid_handler).call_tool(
            "list_work_events",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )
    assert listed["items"] == [same_status_update_event]

    changed_status = {
        **same_status_update_event,
        "id": 44,
        "metadata": {
            "changes": {"status": {"before": "pending", "after": "done"}},
            "work_version": 4,
        },
    }

    def invalid_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [changed_status],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "pre_phase5_history_may_be_incomplete": False,
            },
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, invalid_handler).call_tool(
            "list_work_events",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )


@pytest.mark.parametrize(
    ("field", "before", "after"),
    (
        ("title", "   ", "After"),
        ("title", "Before", "T" * 201),
        ("summary", "   ", "After"),
        ("summary", "Before", "S" * 1001),
    ),
)
def test_work_updated_text_changes_enforce_field_specific_bounds(
    progress_event,
    field,
    before,
    after,
):
    valid_event = {
        **progress_event,
        "event_type": "work_updated",
        "body": None,
        "metadata": {
            "changes": {
                "title": {"before": "Before", "after": "T" * 200},
                "summary": {"before": "Before", "after": "S" * 1000},
            },
            "work_version": 4,
        },
    }
    WorkEventRead.model_validate(valid_event)

    invalid_event = {
        **valid_event,
        "metadata": {
            "changes": {field: {"before": before, "after": after}},
            "work_version": 4,
        },
    }
    with pytest.raises(ValueError):
        WorkEventRead.model_validate(invalid_event)


@pytest.mark.parametrize(
    ("origin", "metadata_key", "metadata"),
    (
        ("live", "expires_at", {"expires_at": EXPIRES_AT}),
        (
            "backfill",
            "observed_expires_at",
            {
                "observed_expires_at": EXPIRES_AT,
                "expiry_basis": "retained_lease_at_cutover",
            },
        ),
    ),
)
def test_claimed_event_expiry_metadata_requires_utc(
    progress_event,
    origin,
    metadata_key,
    metadata,
):
    event = {
        **progress_event,
        "event_type": "work_claimed",
        "body": None,
        "lease_generation_id": RELATIONSHIP_ID,
        "metadata": metadata,
        "origin": origin,
    }
    validated = WorkEventRead.model_validate(event)
    assert validated.metadata.model_dump(mode="json")[metadata_key] == EXPIRES_AT

    for invalid_expiry in ("2026-08-30T12:15:00", "2026-08-30T13:15:00+01:00"):
        invalid_event = {
            **event,
            "metadata": {**metadata, metadata_key: invalid_expiry},
        }
        with pytest.raises(ValueError):
            WorkEventRead.model_validate(invalid_event)


@pytest.mark.parametrize(
    "created_at",
    ("2026-08-30T12:00:00", "2026-08-30T13:00:00+01:00"),
)
def test_event_created_at_requires_utc(progress_event, created_at):
    with pytest.raises(ValueError):
        WorkEventRead.model_validate({**progress_event, "created_at": created_at})


def test_event_lifecycle_and_relationship_projections_are_strict(progress_event):
    same_status_transition = {
        **progress_event,
        "event_type": "work_status_changed",
        "body": None,
        "metadata": {
            "from_status": "wont-do",
            "to_status": "wont-do",
            "changes": {
                "status": {"before": "wont-do", "after": "wont-do"},
            },
            "work_version": 4,
        },
    }
    with pytest.raises(ValueError):
        WorkEventRead.model_validate(same_status_transition)

    relationship_event = {
        **progress_event,
        "event_type": "dependency_added",
        "body": None,
        "relationship_id": RELATIONSHIP_ID,
        "relationship_source_work_item_id": WORK_ID,
        "relationship_target_work_item_id": OTHER_WORK_ID,
        "relationship_direction": "outgoing",
        "counterpart_work_item_id": OTHER_WORK_ID,
        "metadata": {"relationship_type": "blocks"},
    }
    WorkEventRead.model_validate(relationship_event)

    for invalid_projection in (
        {"relationship_direction": "incoming"},
        {"counterpart_work_item_id": WORK_ID},
        {"work_item_id": OTHER_CHECKPOINT_ID},
        {
            "relationship_context_checkpoint_work_item_id": OTHER_CHECKPOINT_ID,
            "relationship_context_checkpoint_id": CHECKPOINT_ID,
        },
    ):
        with pytest.raises(ValueError):
            WorkEventRead.model_validate(
                {
                    **relationship_event,
                    **invalid_projection,
                }
            )


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    (
        ("done", "wont-do"),
        ("done", "promoted"),
        ("wont-do", "wont-do"),
        ("wont-do", "promoted"),
        ("promoted", "wont-do"),
        ("promoted", "promoted"),
    ),
)
def test_work_status_changed_rejects_every_terminal_origin(
    progress_event, from_status, to_status
):
    event = {
        **progress_event,
        "event_type": "work_status_changed",
        "body": None,
        "metadata": {
            "from_status": from_status,
            "to_status": to_status,
            "changes": {
                "status": {"before": from_status, "after": to_status},
            },
            "work_version": 4,
        },
    }
    with pytest.raises(ValueError):
        WorkEventRead.model_validate(event)


def test_relationship_events_enforce_discovery_context_and_related_normalization(
    progress_event,
):
    discovered_event = {
        **progress_event,
        "event_type": "relationship_added",
        "body": None,
        "relationship_id": RELATIONSHIP_ID,
        "relationship_source_work_item_id": OTHER_WORK_ID,
        "relationship_target_work_item_id": WORK_ID,
        "relationship_context_checkpoint_work_item_id": WORK_ID,
        "relationship_context_checkpoint_id": CHECKPOINT_ID,
        "relationship_direction": "incoming",
        "counterpart_work_item_id": OTHER_WORK_ID,
        "metadata": {"relationship_type": "discovered-from"},
    }
    WorkEventRead.model_validate(discovered_event)

    for invalid_context in (
        {
            "relationship_context_checkpoint_work_item_id": None,
            "relationship_context_checkpoint_id": None,
        },
        {
            "relationship_context_checkpoint_work_item_id": OTHER_WORK_ID,
            "relationship_context_checkpoint_id": OTHER_CHECKPOINT_ID,
        },
    ):
        with pytest.raises(ValueError):
            WorkEventRead.model_validate({**discovered_event, **invalid_context})

    related_event = {
        **discovered_event,
        "relationship_context_checkpoint_work_item_id": None,
        "relationship_context_checkpoint_id": None,
        "relationship_direction": "undirected",
        "metadata": {"relationship_type": "related"},
    }
    WorkEventRead.model_validate(related_event)
    for invalid_endpoints in (
        {
            "relationship_source_work_item_id": WORK_ID,
            "relationship_target_work_item_id": OTHER_WORK_ID,
        },
        {
            "relationship_source_work_item_id": WORK_ID,
            "relationship_target_work_item_id": WORK_ID,
            "counterpart_work_item_id": WORK_ID,
        },
    ):
        with pytest.raises(ValueError):
            WorkEventRead.model_validate({**related_event, **invalid_endpoints})


def test_release_event_holder_values_are_validated_without_stripping(progress_event):
    metadata = {
        "lease_holder_kind": "client",
        "lease_holder_client": "  retained-client  ",
        "lease_holder_session_id": "\tretained-session\t",
    }
    event = {
        **progress_event,
        "event_type": "work_released",
        "body": None,
        "lease_generation_id": RELATIONSHIP_ID,
        "lease_release_id": OTHER_CHECKPOINT_ID,
        "metadata": metadata,
    }
    validated = WorkEventRead.model_validate(event).model_dump(mode="json")
    assert validated["metadata"] == metadata

    for field, value in (
        ("lease_holder_client", "   "),
        ("lease_holder_client", "c" * 81),
        ("lease_holder_session_id", "\t\n"),
        ("lease_holder_session_id", "s" * 201),
    ):
        with pytest.raises(ValueError):
            WorkEventRead.model_validate(
                {**event, "metadata": {**metadata, field: value}}
            )


async def test_event_response_rejects_unknown_fields_without_echoing_them(
    settings, progress_event
):
    marker = "private-upstream-event-marker"

    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [{**progress_event, "lease_token": marker}],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "pre_phase5_history_may_be_incomplete": False,
            },
        )

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, handler).call_tool(
            "list_work_events",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    assert marker not in str(caught.value)


async def test_event_tools_reject_responses_outside_requested_scope(
    settings, progress_event
):
    append_arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "body": progress_event["body"],
        **ACTOR_ARGUMENTS,
        **OPERATION_ARGUMENT,
    }
    for override in (
        {"project_id": OTHER_CHECKPOINT_ID},
        {"work_item_id": OTHER_WORK_ID},
    ):
        def append_handler(request, override=override):
            return httpx.Response(201, json={**progress_event, **override})

        with pytest.raises(ToolError, match="complete exact tool argument object"):
            await adapter(settings, append_handler).call_tool(
                "append_event", append_arguments
            )

    for override in ({"work_item_id": OTHER_WORK_ID},):
        def list_handler(request, override=override):
            return httpx.Response(
                200,
                json={
                    "items": [{**progress_event, **override}],
                    "total": 1,
                    "limit": 50,
                    "offset": 0,
                    "pre_phase5_history_may_be_incomplete": False,
                },
            )

        with pytest.raises(ToolError, match="unexpected response"):
            await adapter(settings, list_handler).call_tool(
                "list_work_events",
                {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
            )

    def historical_list_handler(request):
        return httpx.Response(
            200,
            json={
                "items": [{**progress_event, "project_id": OTHER_CHECKPOINT_ID}],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "pre_phase5_history_may_be_incomplete": False,
            },
        )

    historical = structured(
        await adapter(settings, historical_list_handler).call_tool(
            "list_work_events",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )
    assert historical["items"][0]["project_id"] == OTHER_CHECKPOINT_ID


async def test_append_event_validation_and_unknown_outcome_are_value_free(settings):
    marker = "private-event-content-marker"

    def no_request(request):
        pytest.fail("Invalid progress input must not cross the HTTP boundary")

    server = adapter(settings, no_request)
    for arguments, field in (
        ({"body": "   ", **ACTOR_ARGUMENTS}, "body"),
        (
            {
                "body": "Progress",
                "metadata": {"authorization": marker},
                **ACTOR_ARGUMENTS,
            },
            "metadata",
        ),
        (
            {
                "body": "Progress",
                "metadata": {"Client_Operation_ID": marker},
                **ACTOR_ARGUMENTS,
            },
            "metadata",
        ),
        (
            {
                "body": "Progress",
                "metadata": {"gate_id": marker},
                **ACTOR_ARGUMENTS,
            },
            "metadata",
        ),
        (
            {
                "body": "Progress",
                "metadata": {"nested": [{"GaTe_TyPe": marker}]},
                **ACTOR_ARGUMENTS,
            },
            "metadata",
        ),
        (
            {
                "body": "Progress",
                "metadata": {"nested": [f"{marker}\x00"]},
                **ACTOR_ARGUMENTS,
            },
            "metadata",
        ),
        (
            {
                "body": "Progress",
                "event_type": "work_completed",
                **ACTOR_ARGUMENTS,
            },
            "event_type",
        ),
    ):
        with pytest.raises(ToolError) as caught:
            await server.call_tool(
                "append_event",
                {
                    "project_id": PROJECT_ID,
                    "work_item_id": WORK_ID,
                    **arguments,
                    **OPERATION_ARGUMENT,
                },
            )
        assert field in str(caught.value)
        assert marker not in str(caught.value)
        assert "work_completed" not in str(caught.value)

    requests = []

    def unavailable(request):
        requests.append(request)
        raise httpx.ReadTimeout(f"private {marker}", request=request)

    with pytest.raises(ToolError, match="complete exact tool argument object") as caught:
        await adapter(settings, unavailable).call_tool(
            "append_event",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "body": "Progress",
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    assert "every argument unchanged" in str(caught.value)
    assert "do not generate or substitute a new UUID" in str(caught.value)
    assert marker not in str(caught.value)
    assert len(requests) == 1

async def test_get_and_update_work_use_identity_endpoint(settings, work_item):
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}"
        if request.method == "PATCH":
            assert json.loads(request.content) == {
                **OPERATION_PAYLOAD,
                "expected_version": 3,
                "summary": "Narrowed to UUID punctuation.",
                "priority": 9,
                "status": "promoted",
                **ACTOR_PAYLOAD,
            }
            return httpx.Response(
                200,
                json={
                    **work_item,
                    "summary": "Narrowed to UUID punctuation.",
                    "priority": 9,
                    "status": "promoted",
                    "version": 4,
                },
            )
        return httpx.Response(
            200,
            json={
                "work_item": work_item,
                "canonical": {
                    "is_duplicate": False,
                    "direct_destination": None,
                    "canonical_work_item": {
                        "id": WORK_ID,
                        "title": work_item["title"],
                        "status": work_item["status"],
                    },
                    "path": [],
                    "duplicate_member_count": 0,
                },
            },
        )

    server = adapter(settings, handler)
    recalled = structured(
        await server.call_tool("get_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID})
    )
    assert recalled["work_item"]["version"] == 3
    assert recalled["canonical"]["canonical_work_item"]["id"] == WORK_ID
    updated = structured(
        await server.call_tool(
            "update_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "changes": {
                    "summary": "Narrowed to UUID punctuation.",
                    "priority": 9,
                    "status": "promoted",
                },
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert updated["version"] == 4
    assert seen == ["GET", "PATCH"]


async def test_checkpoint_tools_preserve_immutable_context_and_page_history(
    settings, checkpoint
):
    progress = {
        "prompt": "Investigated the race; the focused test now passes.",
        "source_client": "claude-code",
        "source_session_id": "progress-session",
        "source_model": "test-model",
        "source_session_url": None,
        "repository_branch": "fix/search",
        "verified_against": "abcdef1",
        "affected_paths": ["src/search.py", "tests/**"],
        "tags": ["search", "progress"],
        "source_metadata": {"tests": ["focused"]},
    }
    progress_read = {**checkpoint, **progress, "kind": "progress"}
    seen = []

    def handler(request):
        seen.append(request.method)
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/checkpoints"
        )
        if request.method == "GET":
            assert dict(request.url.params) == {"order": "newest", "limit": "20", "offset": "5"}
            return httpx.Response(
                200, json={"items": [progress_read], "total": 6, "limit": 20, "offset": 5}
            )
        assert json.loads(request.content) == {
            **OPERATION_PAYLOAD,
            "kind": "progress",
            **progress,
        }
        return httpx.Response(201, json=progress_read)

    server = adapter(settings, handler)
    listed = structured(
        await server.call_tool(
            "list_checkpoints",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "order": "newest",
                "limit": 20,
                "offset": 5,
            },
        )
    )
    assert listed["items"][0]["prompt"] == progress["prompt"]
    added = structured(
        await server.call_tool(
            "add_checkpoint",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "kind": "progress",
                "checkpoint": progress,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert added["kind"] == "progress"
    assert added["affected_paths"] == ["src/search.py", "tests/**"]
    assert added["source_metadata"] == progress["source_metadata"]
    assert seen == ["GET", "POST"]


async def test_recall_resource_and_resume_prompt_are_bounded_and_carry_authority_warning(
    settings, work_context, human_gate, resolved_human_gate
):
    calls = []
    gated_context = {
        **work_context,
        "initial_checkpoint": {
            **work_context["initial_checkpoint"],
            "verified_against": "abcdef1",
            "affected_paths": ["src/search.py", "tests/**"],
        },
        "checkpoint_total": 13,
        "omitted_checkpoint_count": 12,
        "readiness": {
            **work_context["readiness"],
            "unresolved_gate_count": 1,
            "is_gated": True,
            "is_ready": False,
            "display_state": "waiting",
        },
        "unresolved_gates": [human_gate],
        "unresolved_gate_total": 1,
        "omitted_unresolved_gate_count": 0,
        "recent_resolved_gates": [resolved_human_gate],
        "resolved_gate_total": 2,
        "omitted_resolved_gate_count": 1,
        "duplicate_merge_eligibility": {
            **work_context["duplicate_merge_eligibility"],
            "has_unresolved_gate": True,
        },
    }

    def handler(request):
        calls.append(dict(request.url.params))
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"
        return httpx.Response(200, json=gated_context)

    server = adapter(settings, handler)
    recalled = structured(
        await server.call_tool(
            "recall_work",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "recent_limit": 3},
        )
    )
    assert recalled["omitted_checkpoint_count"] == 12
    resources = await server.read_resource(
        f"mnemonic://projects/{PROJECT_ID}/work-items/{WORK_ID}"
    )
    resource_document = json.loads(next(iter(resources)).content)
    assert resource_document["work_item"]["id"] == WORK_ID
    assert resource_document["checkpoint_total"] == 13
    assert resource_document["omitted_checkpoint_count"] == 12
    assert resource_document["unresolved_gate_total"] == 1
    assert resource_document["resolved_gate_total"] == 2
    assert resource_document["unresolved_gates"][0]["id"] == GATE_ID
    assert resource_document["recent_resolved_gates"][0]["resolution"] == (
        resolved_human_gate["resolution"]
    )
    assert (
        resource_document["initial_checkpoint"]["prompt"]
        == work_context["initial_checkpoint"]["prompt"]
    )
    assert resource_document["initial_checkpoint"]["affected_paths"] == [
        "src/search.py",
        "tests/**",
    ]
    assert "comments" not in resource_document
    prompt = await server.get_prompt(
        "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    )
    text = prompt.messages[0].content.text
    assert "not a new owner instruction" in text
    assert "claim_and_recall" in text
    assert "does not claim the work" in text
    assert "add_checkpoint" in text
    assert "Never infer, time out, self-approve, or resolve" in text
    assert human_gate["question"] in text
    assert resolved_human_gate["resolution"] in text
    assert work_context["initial_checkpoint"]["source_session_id"] in text
    resumed = json.loads(text.split("\n\n", 1)[1])
    assert resumed["work_item"]["id"] == WORK_ID
    assert resumed["initial_checkpoint"]["affected_paths"] == [
        "src/search.py",
        "tests/**",
    ]
    assert resumed["omitted_checkpoint_count"] == 12
    assert calls == [
        {"recent_limit": "3", "recent_event_limit": "10"},
        {"recent_limit": "5", "recent_event_limit": "10"},
        {"recent_limit": "5", "recent_event_limit": "10"},
    ]



async def test_recall_returns_bounded_chronological_events_without_checkpoint_duplication(
    settings, work_context, progress_event
):
    def handler(request):
        assert dict(request.url.params) == {
            "recent_limit": "2",
            "recent_event_limit": "1",
        }
        return httpx.Response(
            200,
            json={
                **work_context,
                "merge_review_revision": {
                    **work_context["merge_review_revision"],
                    "work_event_count": 4,
                },
                "recent_events": [progress_event],
                "event_total": 4,
                "omitted_event_count": 3,
                "pre_phase5_history_may_be_incomplete": True,
            },
        )

    recalled = structured(
        await adapter(settings, handler).call_tool(
            "recall_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "recent_limit": 2,
                "recent_event_limit": 1,
            },
        )
    )
    assert recalled["current_context"] is None
    assert recalled["current_context_is_initial"] is True
    assert recalled["recent_checkpoints"] == []
    assert recalled["recent_events"] == [progress_event]
    assert recalled["event_total"] == 4
    assert recalled["omitted_event_count"] == 3
    assert recalled["pre_phase5_history_may_be_incomplete"] is True
    recalled_checkpoints = [
        recalled["initial_checkpoint"],
        recalled["current_context"],
        *recalled["recent_checkpoints"],
    ]
    assert sum(
        checkpoint is not None
        and checkpoint["id"] == work_context["initial_checkpoint"]["id"]
        for checkpoint in recalled_checkpoints
    ) == 1


async def test_claim_tools_send_body_only_and_return_exact_capability_models(
    settings, claim_receipt, active_work_context
):
    claim_body = {
        "holder_client": claim_receipt["holder_client"],
        "holder_session_id": claim_receipt["holder_session_id"],
        "claim_request_id": claim_receipt["claim_request_id"],
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.method == "POST"
        assert not request.url.params
        assert CLAIM_REQUEST_ID not in str(request.url)
        assert LEASE_TOKEN not in str(request.url)
        assert json.loads(request.content) == claim_body
        if request.url.path.endswith("/claim-and-recall"):
            return httpx.Response(
                200,
                json={"lease": claim_receipt, "context": active_work_context},
            )
        assert request.url.path.endswith("/claim")
        return httpx.Response(200, json=claim_receipt)

    server = adapter(settings, handler)
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        **claim_body,
    }
    claimed = structured(await server.call_tool("claim_work", arguments))
    assert claimed == claim_receipt
    claimed_context = structured(await server.call_tool("claim_and_recall", arguments))
    assert claimed_context["lease"] == claim_receipt
    assert claimed_context["context"]["readiness"]["display_state"] == "active"
    assert LEASE_TOKEN not in json.dumps(claimed_context["context"])
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/claim",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/claim-and-recall",
    ]


async def test_renew_and_release_send_exact_json_bodies(
    settings, claim_receipt
):
    renewed_receipt = {
        **claim_receipt,
        "renewed_at": "2026-08-30T12:05:00Z",
        "expires_at": "2026-08-30T12:20:00Z",
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert request.method == "POST"
        assert not request.url.params
        assert LEASE_TOKEN not in str(request.url)
        body = json.loads(request.content)
        expected = {"lease_token": LEASE_TOKEN}
        if request.url.path.endswith("/renew-claim"):
            assert body == expected
            return httpx.Response(200, json=renewed_receipt)
        assert request.url.path.endswith("/release-claim")
        assert body == {**OPERATION_PAYLOAD, **expected, **ACTOR_PAYLOAD}
        return httpx.Response(200, json={"work_item_id": WORK_ID, "released": False})

    server = adapter(settings, handler)
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "lease_token": LEASE_TOKEN,
    }
    renewed = structured(await server.call_tool("renew_claim", arguments))
    assert renewed == renewed_receipt
    released = structured(
        await server.call_tool(
            "release_claim",
            {**arguments, **ACTOR_ARGUMENTS, **OPERATION_ARGUMENT},
        )
    )
    assert released == {"work_item_id": WORK_ID, "released": False}
    assert "lease_token" not in released
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/renew-claim",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/release-claim",
    ]


async def test_complete_and_delete_work_return_explicit_mutation_receipts(
    settings, work_item, checkpoint
):
    completion_input = {
        "prompt": "Fixed the query and observed focused and full suites pass.",
        "source_client": "claude-code",
        "source_session_id": "completing-session",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": "fix/search",
        "verified_against": "abcdef1",
        "affected_paths": ["src/search.py", "tests/**"],
        "tags": ["done"],
        "source_metadata": {"tests": ["focused", "full"]},
    }
    completed_work = {**work_item, "status": "done", "version": 4}
    completion_checkpoint = {**checkpoint, **completion_input, "kind": "completion"}
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/complete"):
            assert request.method == "POST"
            assert json.loads(request.content) == {
                **OPERATION_PAYLOAD,
                "expected_version": 3,
                "checkpoint": completion_input,
            }
            return httpx.Response(
                200,
                json={"work_item": completed_work, "checkpoint": completion_checkpoint},
            )
        assert request.url.path.endswith("/delete")
        assert json.loads(request.content) == {
            **OPERATION_PAYLOAD,
            "expected_version": 4,
            **ACTOR_PAYLOAD,
        }
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "version": 5,
            },
        )

    server = adapter(settings, handler)
    completed = structured(
        await server.call_tool(
            "complete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "checkpoint": completion_input,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert completed["work_item"]["status"] == "done"
    assert completed["checkpoint"]["kind"] == "completion"
    assert completed["checkpoint"]["affected_paths"] == [
        "src/search.py",
        "tests/**",
    ]
    deleted = structured(
        await server.call_tool(
            "delete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 4,
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    )
    assert deleted == {
        "deleted": True,
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "version": 5,
    }
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/complete",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete",
    ]


async def test_canonical_mutations_send_optional_lease_token_only_in_body(
    settings, work_item, checkpoint
):
    checkpoint_input = {
        name: checkpoint[name]
        for name in (
            "prompt",
            "source_client",
            "source_session_id",
            "source_model",
            "source_session_url",
            "repository_branch",
            "verified_against",
            "tags",
            "source_metadata",
        )
    }
    seen = []

    def handler(request):
        seen.append(request.url.path)
        assert LEASE_TOKEN not in str(request.url)
        assert json.loads(request.content)["lease_token"] == LEASE_TOKEN
        if request.url.path.endswith("/checkpoints"):
            return httpx.Response(201, json=checkpoint)
        if request.method == "PATCH":
            assert json.loads(request.content)["actor"] == ACTOR_PAYLOAD["actor"]
            return httpx.Response(
                200,
                json={**work_item, "status": "promoted", "version": 4},
            )
        if request.url.path.endswith("/complete"):
            return httpx.Response(
                200,
                json={
                    "work_item": {**work_item, "status": "done", "version": 4},
                    "checkpoint": {**checkpoint, "kind": "completion"},
                },
            )
        assert request.url.path.endswith("/delete")
        assert json.loads(request.content)["actor"] == ACTOR_PAYLOAD["actor"]
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "version": 4,
            },
        )

    server = adapter(settings, handler)
    common = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "lease_token": LEASE_TOKEN,
        **OPERATION_ARGUMENT,
    }
    await server.call_tool(
        "add_checkpoint",
        {**common, "checkpoint": checkpoint_input},
    )
    await server.call_tool(
        "update_work",
        {**common, "expected_version": 3, "changes": {"status": "promoted"}, **ACTOR_ARGUMENTS},
    )
    await server.call_tool(
        "complete_work",
        {**common, "expected_version": 3, "checkpoint": checkpoint_input},
    )
    await server.call_tool(
        "delete_work", {**common, "expected_version": 3, **ACTOR_ARGUMENTS}
    )
    assert seen == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/checkpoints",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/complete",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete",
    ]


@pytest.mark.parametrize("status", ["all", "active", "dropped"])
async def test_search_passes_explicit_filters_and_pagination(settings, status):
    def handler(request):
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items"
        assert dict(request.url.params) == {
            "status": status, "tag": "search", "source_client": "opencode",
            "source_session_id": "ses_123/opaque", "view": "full", "limit": "5",
            "duplicate_scope": "canonical", "offset": "10", "semantic": "true",
        }
        assert request.extensions["timeout"]["read"] == 60.0
        assert request.extensions["timeout"]["connect"] == 5.0
        return httpx.Response(200, json={"items": [], "total": 10, "limit": 5, "offset": 10})

    await adapter(settings, handler).call_tool("search_work", {
        "project_id": PROJECT_ID, "status": status, "tag": "search", "source_client": "opencode",
        "source_session_id": "ses_123/opaque", "view": "full", "semantic": True,
        "limit": 5, "offset": 10,
    })


async def test_search_defaults_to_full_canonical_hits(settings, work_summary):
    seen: list[dict[str, str]] = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "items": [{
                    "summary": work_summary,
                    "matched_member": {
                        "id": WORK_ID,
                        "title": work_summary["work_item"]["title"],
                        "status": "pending",
                    },
                }],
                "total": 1,
                "limit": 30,
                "offset": 0,
            },
        )

    server = adapter(settings, handler)
    default = structured(await server.call_tool("search_work", {"project_id": PROJECT_ID}))
    hit = default["items"][0]
    assert hit["summary"]["current_context"]["id"] == work_summary["current_context"]["id"]
    assert hit["matched_member"]["id"] == WORK_ID
    assert seen == [{
        "status": "pending",
        "view": "full",
        "duplicate_scope": "canonical",
        "limit": "30",
        "offset": "0",
    }]



async def test_httpx_debug_logs_never_include_query_or_cursor_values(settings, caplog):
    query_marker = "private-search-query-marker"
    cursor_marker = "private-gate-cursor-marker"

    def handler(request):
        if request.url.path.endswith("/work-items"):
            return httpx.Response(
                200,
                json={"items": [], "total": 0, "limit": 30, "offset": 0},
            )
        return httpx.Response(
            200,
            json={"items": [], "total": 0, "limit": 30, "next_cursor": None},
        )

    caplog.set_level("DEBUG")
    server = adapter(settings, handler)
    await server.call_tool(
        "search_work", {"project_id": PROJECT_ID, "q": query_marker}
    )
    await server.call_tool(
        "list_work_gates",
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "cursor": cursor_marker,
        },
    )

    assert query_marker not in caplog.text
    assert cursor_marker not in caplog.text


async def test_semantic_search_unavailable_suggests_lexical_fallback(settings):
    def handler(request):
        assert request.url.params["semantic"] == "true"
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": "semantic_unavailable",
                    "message": f"private detail {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match="semantic search is unavailable") as caught:
        await adapter(settings, handler).call_tool("search_work", {
            "project_id": PROJECT_ID, "q": "conceptual query", "semantic": True,
        })
    assert API_KEY not in str(caught.value)


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"prompt": "rewrite history"},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"source_session_id": "forged-session"},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"summary": None},
        },
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            "changes": {"status": "done"},
        },
    ],
)
async def test_update_rejects_empty_and_immutable_fields(settings, arguments):
    def handler(request):
        pytest.fail("Invalid or immutable fields must not cross the HTTP boundary")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("update_work", arguments)


async def test_delete_passes_version_and_conflict_is_not_retried(settings):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/delete"
        )
        assert json.loads(request.content) == {
            **OPERATION_PAYLOAD,
            "expected_version": 3,
            **ACTOR_PAYLOAD,
        }
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "version_conflict",
                    "message": "internal database version details",
                    "context": {},
                }
            },
        )

    with pytest.raises(ToolError, match="Version conflict") as caught:
        await adapter(settings, handler).call_tool("delete_work", {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "expected_version": 3,
            **ACTOR_ARGUMENTS,
            **OPERATION_ARGUMENT,
        })
    assert "internal database" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "code, expected",
    [
        ("version_conflict", "Version conflict"),
        ("work_not_pending", "not pending"),
        ("work_blocked", "unresolved blocker"),
        ("work_gated", "unresolved human input"),
        ("invalid_status_transition", "lifecycle transition is not allowed"),
        ("completion_episode_unsealed", "cannot be reopened"),
        ("lease_expired", "claim has expired"),
        ("lease_token_mismatch", "does not match"),
        ("claim_request_expired", "new claim_request_id"),
        ("relationship_cycle", "create a cycle"),
        ("relationship_context_invalid", "originating target work item"),
        ("parent_already_set", "already has a parent"),
        ("active_relationships", "relationships before deleting"),
    ],
)
async def test_typed_application_errors_are_actionable_and_sanitized(
    settings, code, expected
):
    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool(
            "delete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    assert API_KEY not in str(caught.value)


async def test_gate_secret_errors_are_value_free_and_single_attempt(
    settings, human_gate
):
    private_marker = "private-human-gate-diagnostic"
    calls = []

    def secret_handler(request):
        calls.append(request)
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "gate_secret_echo",
                    "message": private_marker,
                    "context": {"question": human_gate["question"]},
                }
            },
        )

    with pytest.raises(
        ToolError, match="request-known credential or operation control"
    ) as caught:
        await adapter(settings, secret_handler).call_tool(
            "request_human_input", protected_tool_arguments()["request_human_input"]
        )
    assert private_marker not in str(caught.value)
    assert human_gate["question"] not in str(caught.value)
    assert len(calls) == 1


async def test_gate_cursor_errors_are_scoped_and_sanitized(settings):
    private_marker = "private-gate-scope-diagnostic"

    def cursor_handler(request):
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "invalid_cursor",
                    "message": private_marker,
                    "context": {"cursor": private_marker},
                }
            },
        )

    with pytest.raises(ToolError, match="invalid for this project") as caught:
        await adapter(settings, cursor_handler).call_tool(
            "list_work_gates",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "cursor": "validly-shaped-but-wrong-scope",
            },
        )
    assert private_marker not in str(caught.value)


@pytest.mark.parametrize("code", ["lease_held", "work_move_active_lease"])
async def test_lease_contention_reports_only_public_coordination(settings, code):
    private_url = "https://internal.invalid/session/private"

    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {private_url} {LEASE_TOKEN}",
                    "context": {
                        "holder_client": "other-client",
                        "expires_at": EXPIRES_AT,
                        "holder_session_id": "other-session",
                        "purpose": "code_review",
                        "code_review_id": WORK_ID,
                        "mode": "cold",
                        "claim_request_id": CLAIM_REQUEST_ID,
                        "lease_token": LEASE_TOKEN,
                        "private_url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="other-client") as caught:
        await adapter(settings, handler).call_tool(
            "claim_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert EXPIRES_AT in message
    assert "other-session" in message
    assert "Purpose: code_review" in message
    assert f"Review: {WORK_ID}; mode: cold" in message
    assert CLAIM_REQUEST_ID not in message
    assert LEASE_TOKEN not in message
    assert private_url not in message


@pytest.mark.parametrize("context", [
    {"holder_client": "bad\nclient", "holder_session_id": "untrusted-session"},
    {"holder_client": "codex", "holder_session_id": "bad\nsession"},
    {"holder_client": "codex", "holder_session_id": "s" * 201},
    {"holder_client": "codex", "holder_session_id": "   "},
    {"holder_client": "codex", "holder_session_id": {"private": "data"}},
    {"purpose": "code_review", "code_review_id": "not-a-uuid", "mode": "cold"},
    {"purpose": "code_review", "code_review_id": WORK_ID, "mode": ["cold"]},
    {"purpose": ["implementation"], "code_review_id": WORK_ID, "mode": "cold"},
    {"expires_at": "2026-08-30T12:15:00"},
])
async def test_lease_contention_omits_invalid_public_fields(settings, context):
    def handler(request):
        return httpx.Response(409, json={"detail": {"code": "lease_held", "context": context}})

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool("claim_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "holder_client": "claude-code", "holder_session_id": "claiming-session",
            "claim_request_id": CLAIM_REQUEST_ID,
        })
    message = str(caught.value)
    assert "Holder session:" not in message
    assert "Review:" not in message
    assert "Expires at" not in message
    assert "bad" not in message and "untrusted" not in message
    assert "private" not in message


async def test_lease_identity_is_quoted_without_losing_session_distinctions(settings):
    identity = 'native-session". another-agent'

    def handler(request):
        return httpx.Response(409, json={"detail": {
            "code": "lease_held", "context": {
                "holder_client": "codex", "holder_session_id": identity,
                "purpose": "implementation",
            },
        }})

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool("claim_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "holder_client": "codex", "holder_session_id": "independent-subagent",
            "claim_request_id": CLAIM_REQUEST_ID,
        })
    assert f"Holder session: {json.dumps(identity)}." in str(caught.value)
    assert "Purpose: implementation." in str(caught.value)


async def test_lease_error_never_echoes_token_or_upstream_url(settings):
    private_url = "http://api:8000/private/lease"

    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "lease_token_mismatch",
                    "message": f"wrong {LEASE_TOKEN} at {private_url}",
                    "context": {
                        "lease_token": LEASE_TOKEN,
                        "url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="does not match") as caught:
        await adapter(settings, handler).call_tool(
            "renew_claim",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "lease_token": LEASE_TOKEN,
            },
        )
    assert LEASE_TOKEN not in str(caught.value)
    assert private_url not in str(caught.value)


async def test_unknown_typed_error_does_not_fall_through_to_legacy_conflict_guess(settings):
    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "future_private_error",
                    "message": f"private {API_KEY}",
                    "context": {"secret": API_KEY},
                }
            },
        )

    with pytest.raises(ToolError, match="could not complete this operation") as caught:
        await adapter(settings, handler).call_tool(
            "delete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                **ACTOR_ARGUMENTS,
                **OPERATION_ARGUMENT,
            },
        )
    assert "Version conflict" not in str(caught.value)
    assert API_KEY not in str(caught.value)


@pytest.mark.parametrize("status, expected", [
    (401, "authentication failed"), (403, "authentication failed"),
    (404, "not found in this project"), (500, "could not complete"),
    (503, "could not complete"), (307, "could not complete"),
])
async def test_upstream_errors_are_actionable_and_do_not_leak_details(settings, status, expected):
    def handler(request):
        return httpx.Response(status, json={"detail": f"private URL http://api:8000 and {API_KEY}"})

    with pytest.raises(ToolError, match=expected) as caught:
        await adapter(settings, handler).call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    assert API_KEY not in str(caught.value)
    assert "http://api:8000" not in str(caught.value)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("project_not_found", "Project not found"),
        ("work_item_not_found", "Work item not found in this project"),
        ("checkpoint_not_found", "Checkpoint not found on this work item"),
        ("relationship_not_found", "Relationship not found in this project"),
    ],
)
async def test_not_found_names_the_entity_kind_that_missed(settings, code, expected):
    """An agent must know whether to re-resolve the project or re-search within it."""

    def handler(request):
        return httpx.Response(
            404,
            json={
                "detail": {
                    "code": code,
                    "message": f"private detail {API_KEY}",
                    "context": {},
                }
            },
        )

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    message = str(caught.value)
    assert expected in message
    # Naming the kind must not start echoing identifiers or upstream detail.
    assert API_KEY not in message
    assert PROJECT_ID not in message
    assert WORK_ID not in message


async def test_unknown_not_found_code_falls_back_without_guessing(settings):
    def handler(request):
        return httpx.Response(404, json={"detail": f"private {API_KEY}"})

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    message = str(caught.value)
    assert "was not found in this project" in message
    assert API_KEY not in message


async def test_validation_error_reports_the_pydantic_kind_without_the_value(settings):
    def handler(request):
        return httpx.Response(422, json={"detail": [{
            "loc": ["body", "summary"], "type": "string_too_long",
            "msg": API_KEY, "input": API_KEY,
        }]})

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool("list_projects", {})
    message = str(caught.value)
    assert "summary (string_too_long)" in message
    assert API_KEY not in message


async def test_validation_error_names_fields_without_echoing_input(settings):
    def handler(request):
        return httpx.Response(422, json={"detail": [{
            "loc": ["body", "source_metadata"], "msg": API_KEY, "input": API_KEY,
        }]})

    with pytest.raises(ToolError, match="source_metadata") as caught:
        await adapter(settings, handler).call_tool("list_projects", {})
    assert API_KEY not in str(caught.value)


async def test_relationship_validation_errors_name_only_allowlisted_fields(settings):
    fields = {
        "relationship_id",
        "relationship_type",
        "source_work_item_id",
        "target_work_item_id",
        "other_work_item_id",
        "context_checkpoint_id",
        "initial_relationships",
        "type",
        "direction",
        "created_by_client",
        "created_by_session_id",
        "created_by_model",
    }

    def handler(request):
        return httpx.Response(
            422,
            json={
                "detail": [
                    {"loc": ["body", field], "msg": API_KEY, "input": API_KEY}
                    for field in fields
                ]
            },
        )

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool("list_projects", {})
    message = str(caught.value)
    for field in fields:
        assert field in message
    assert API_KEY not in message


@pytest.mark.parametrize(
    "tool_name,arguments,fields,secrets,kinds",
    LOCAL_VALIDATION_CASES,
    ids=[case[0] for case in LOCAL_VALIDATION_CASES],
)
async def test_local_validation_is_strict_and_never_echoes_values(
    settings, tool_name, arguments, fields, secrets, kinds
):
    def handler(request):
        pytest.fail("Locally invalid tool input must not cross the HTTP boundary")

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)

    message = str(caught.value)
    assert message == expected_validation_message(fields, kinds)
    for secret in secrets:
        assert secret not in message
    assert "input_value" not in message
    assert "input_type" not in message
    assert "errors.pydantic.dev" not in message


@pytest.mark.parametrize("invalid_token", [LEASE_TOKEN + "private-suffix" * 20, 123456789])
async def test_invalid_lease_token_is_redacted_before_the_rest_boundary(
    settings, invalid_token
):
    def handler(request):
        pytest.fail("An invalid capability must not cross the HTTP boundary")

    with pytest.raises(ToolError, match="lease_token") as caught:
        await adapter(settings, handler).call_tool(
            "renew_claim",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "lease_token": invalid_token,
            },
        )
    message = str(caught.value)
    assert str(invalid_token) not in message
    assert "errors.pydantic.dev" not in message


async def test_write_network_failure_explains_unknown_outcome_and_does_not_retry(settings):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(f"private transport error: {API_KEY}", request=request)

    with pytest.raises(ToolError, match="write outcome is unknown") as caught:
        await adapter(settings, handler).call_tool("create_project", {"name": "Example"})
    assert API_KEY not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("outcome", ["network", "unavailable", "malformed_success"])
async def test_protected_unknown_outcomes_require_retained_key_and_exact_arguments_once(
    settings, outcome
):
    requests = []
    private_marker = f"private-protected-outcome-{outcome}"

    def handler(request):
        requests.append(request)
        if outcome == "network":
            raise httpx.ReadTimeout(private_marker, request=request)
        if outcome == "unavailable":
            return httpx.Response(
                503,
                json={
                    "detail": {
                        "code": "client_operation_unavailable",
                        "message": private_marker,
                        "context": {"private": private_marker},
                    }
                },
            )
        return httpx.Response(200, json={"private": private_marker})

    with pytest.raises(ToolError, match="complete exact tool argument object") as caught:
        await adapter(settings, handler).call_tool(
            "release_claim", protected_tool_arguments()["release_claim"]
        )

    message = str(caught.value)
    assert "every argument unchanged" in message
    assert "If either was lost" in message
    assert "do not generate or substitute a new UUID" in message
    assert CLIENT_OPERATION_ID not in message
    assert LEASE_TOKEN not in message
    assert private_marker not in message
    assert len(requests) == 1


async def test_deep_json_success_cannot_escape_unknown_outcome_guidance(settings):
    requests = []
    deeply_nested_json = "[" * 10_000 + "0" + "]" * 10_000

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=deeply_nested_json,
            headers={"content-type": "application/json"},
        )

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "release_claim", protected_tool_arguments()["release_claim"]
        )

    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert "RecursionError" not in message
    assert CLIENT_OPERATION_ID not in message
    assert LEASE_TOKEN not in message
    assert len(requests) == 1


@pytest.mark.parametrize(
    "tool_name,checkpoint_field",
    [
        ("create_work", "initial_checkpoint"),
        ("add_checkpoint", "checkpoint"),
        ("complete_work", "checkpoint"),
    ],
)
@pytest.mark.parametrize(
    "reserved_key",
    [
        "LeAsE_ToKeN",
        "CLAIM_REQUEST_ID",
        "Client_Operation_Id",
        "Authorization",
        "API_KEY",
        "Cookie",
        "SeCrEt",
    ],
)
async def test_checkpoint_reserved_metadata_is_rejected_before_http(
    settings,
    tool_name,
    checkpoint_field,
    reserved_key,
):
    arguments = protected_tool_arguments()[tool_name]
    arguments[checkpoint_field] = {
        **arguments[checkpoint_field],
        "source_metadata": {"nested": [{reserved_key: "opaque"}]},
    }

    def handler(request):
        pytest.fail("Reserved checkpoint metadata must not cross the HTTP boundary")

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)

    message = str(caught.value)
    assert "source_metadata" in message
    assert reserved_key not in message
    assert CLIENT_OPERATION_ID not in message


@pytest.mark.parametrize("tool_name", PROTECTED_TOOL_NAMES)
async def test_protected_success_responses_are_canonical_and_request_coherent(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
):
    requests = []
    responses = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )
    status_code = 201 if tool_name in {
        "create_work",
        "add_checkpoint",
        "append_event",
        "request_human_input",
        "merge_work",
    } else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=responses[tool_name])

    result = structured(
        await adapter(settings, handler).call_tool(
            tool_name, protected_tool_arguments()[tool_name]
        )
    )
    assert result == responses[tool_name]
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("tool_name", "checkpoint_path"),
    [
        ("create_work", ("initial_checkpoint",)),
        ("add_checkpoint", ()),
        ("complete_work", ("checkpoint",)),
    ],
)
async def test_checkpoint_mutations_reject_explicit_empty_scope_in_success(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
    checkpoint_path,
):
    response = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )[tool_name]
    target = response
    for field in checkpoint_path:
        target = target[field]
    target["affected_paths"] = []
    requests = []
    status_code = 201 if tool_name in {"create_work", "add_checkpoint"} else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            tool_name, protected_tool_arguments()[tool_name]
        )

    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("tool_name", "checkpoint_path"),
    [
        ("create_work", ("initial_checkpoint",)),
        ("add_checkpoint", ()),
        ("complete_work", ("checkpoint",)),
    ],
)
@pytest.mark.parametrize("explicit_empty_request", [False, True])
async def test_checkpoint_mutations_reject_unexpected_nonempty_response_scope(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
    checkpoint_path,
    explicit_empty_request,
):
    arguments = protected_tool_arguments()[tool_name]
    request_checkpoint = arguments[
        "initial_checkpoint" if tool_name == "create_work" else "checkpoint"
    ]
    request_checkpoint["verified_against"] = "abcdef1"
    if explicit_empty_request:
        request_checkpoint["affected_paths"] = []

    response = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )[tool_name]
    target = response
    for field in checkpoint_path:
        target = target[field]
    target["verified_against"] = "abcdef1"
    target["affected_paths"] = ["unexpected/**"]
    requests = []
    status_code = 201 if tool_name in {"create_work", "add_checkpoint"} else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)

    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("tool_name", "checkpoint_path"),
    [
        ("create_work", ("initial_checkpoint",)),
        ("add_checkpoint", ()),
        ("complete_work", ("checkpoint",)),
    ],
)
async def test_checkpoint_mutations_reject_missing_requested_response_scope(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
    checkpoint_path,
):
    arguments = protected_tool_arguments()[tool_name]
    request_checkpoint = arguments[
        "initial_checkpoint" if tool_name == "create_work" else "checkpoint"
    ]
    request_checkpoint["verified_against"] = "abcdef1"
    request_checkpoint["affected_paths"] = ["required/**"]

    response = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )[tool_name]
    target = response
    for field in checkpoint_path:
        target = target[field]
    target["verified_against"] = "abcdef1"
    target.pop("affected_paths", None)
    requests = []
    status_code = 201 if tool_name in {"create_work", "add_checkpoint"} else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)

    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(caught.value)
    assert "KeyError" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("tool_name", PROTECTED_TOOL_NAMES)
async def test_protected_noncanonical_success_is_an_unknown_outcome_without_retry(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
):
    requests = []
    response = json.loads(
        json.dumps(
            protected_success_responses(
                work_item, checkpoint, relationship, progress_event, human_gate
            )[tool_name]
        )
    )
    timestamp_paths = {
        "create_work": ("work_item", "created_at"),
        "add_checkpoint": ("created_at",),
        "append_event": ("created_at",),
        "add_relationship": ("relationship", "created_at"),
        "update_work": ("created_at",),
        "complete_work": ("checkpoint", "created_at"),
        "request_human_input": ("created_at",),
        "merge_work": ("merge", "created_at"),
    }
    timestamp_path = timestamp_paths.get(tool_name)
    if timestamp_path is not None:
        target = response
        for field in timestamp_path[:-1]:
            target = target[field]
        target[timestamp_path[-1]] = "2026-08-30T12:00:00+00:00"
    elif tool_name == "delete_work":
        response["version"] = "4"
    elif tool_name == "remove_relationship":
        response["removed"] = "true"
    elif tool_name == "release_claim":
        response["released"] = "true"
    status_code = 201 if tool_name in {
        "create_work",
        "add_checkpoint",
        "append_event",
        "request_human_input",
        "merge_work",
    } else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            tool_name, protected_tool_arguments()[tool_name]
        )

    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert CLIENT_OPERATION_ID not in message
    assert LEASE_TOKEN not in message
    assert len(requests) == 1


@pytest.mark.parametrize("tool_name", PROTECTED_TOOL_NAMES)
async def test_protected_incoherent_success_is_an_unknown_outcome_without_retry(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    tool_name,
):
    requests = []
    response = json.loads(
        json.dumps(
            protected_success_responses(
                work_item, checkpoint, relationship, progress_event, human_gate
            )[tool_name]
        )
    )
    if tool_name == "create_work":
        response["work_item"]["project_id"] = OTHER_WORK_ID
    elif tool_name in {"add_checkpoint", "append_event"}:
        response["work_item_id"] = OTHER_WORK_ID
    elif tool_name == "add_relationship":
        response["relationship"]["project_id"] = OTHER_WORK_ID
    elif tool_name == "update_work":
        response["id"] = OTHER_WORK_ID
    elif tool_name == "complete_work":
        response["work_item"]["id"] = OTHER_WORK_ID
    elif tool_name == "delete_work":
        response["work_item_id"] = OTHER_WORK_ID
    elif tool_name == "remove_relationship":
        response["relationship_id"] = OTHER_WORK_ID
    elif tool_name == "merge_work":
        response["merge"]["source_work_item_id"] = OTHER_WORK_ID
    else:
        response["work_item_id"] = OTHER_WORK_ID
    status_code = 201 if tool_name in {
        "create_work",
        "add_checkpoint",
        "append_event",
        "request_human_input",
        "merge_work",
    } else 200

    def handler(request):
        requests.append(request)
        return httpx.Response(status_code, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            tool_name, protected_tool_arguments()[tool_name]
        )

    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert CLIENT_OPERATION_ID not in message
    assert LEASE_TOKEN not in message
    assert len(requests) == 1


async def test_reverse_related_no_op_accepts_original_edge_provenance(
    settings, relationship
):
    arguments = {
        **protected_tool_arguments()["add_relationship"],
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "relationship_type": "related",
        "created_by_client": "new-caller",
        "created_by_session_id": "new-session",
        "context_checkpoint_id": None,
    }
    original = {
        **relationship,
        "project_id": OTHER_CHECKPOINT_ID,
        "relationship_type": "related",
        "source_work_item_id": OTHER_WORK_ID,
        "target_work_item_id": WORK_ID,
        "created_by_client": "original-caller",
        "created_by_session_id": "original-session",
        "created_by_model": "original-model",
        "context_checkpoint_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
    }

    def handler(request):
        return httpx.Response(
            200, json={"relationship": original, "created": False}
        )

    result = structured(
        await adapter(settings, handler).call_tool("add_relationship", arguments)
    )
    assert result == {"relationship": original, "created": False}


async def test_create_work_accepts_normalized_deduplicated_related_edges(
    settings, work_item, checkpoint, relationship, progress_event, human_gate
):
    arguments = protected_tool_arguments()["create_work"]
    arguments["initial_relationships"] = [
        {
            "type": "related",
            "direction": "outgoing",
            "other_work_item_id": OTHER_WORK_ID,
        },
        {
            "type": "related",
            "direction": "incoming",
            "other_work_item_id": OTHER_WORK_ID,
        },
    ]

    response = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )["create_work"]
    response["initial_relationships"] = [
        {
            **relationship,
            "relationship_type": "related",
            "source_work_item_id": OTHER_WORK_ID,
            "target_work_item_id": WORK_ID,
            "created_by_client": arguments["initial_checkpoint"]["source_client"],
            "created_by_session_id": arguments["initial_checkpoint"][
                "source_session_id"
            ],
            "created_by_model": None,
            "context_checkpoint_work_item_id": None,
            "context_checkpoint_id": None,
        }
    ]

    def handler(request):
        return httpx.Response(201, json=response)

    result = structured(
        await adapter(settings, handler).call_tool("create_work", arguments)
    )
    assert result["initial_relationships"] == response["initial_relationships"]


async def test_client_operation_conflict_is_a_redacted_safety_incident(settings):
    requests = []
    private_marker = "private-conflicting-operation-detail"

    def handler(request):
        requests.append(request)
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "client_operation_conflict",
                    "message": private_marker,
                    "context": {
                        "client_operation_id": CLIENT_OPERATION_ID,
                        "private": private_marker,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="caller-safety incident") as caught:
        await adapter(settings, handler).call_tool(
            "update_work", protected_tool_arguments()["update_work"]
        )

    message = str(caught.value)
    assert "do not retry or generate a replacement UUID" in message
    assert CLIENT_OPERATION_ID not in message
    assert private_marker not in message
    assert len(requests) == 1


async def test_client_operation_secret_echo_is_a_redacted_definite_rejection(settings):
    private_marker = "private-operation-secret-echo-detail"

    def handler(request):
        return httpx.Response(
            422,
            json={
                "detail": {
                    "code": "client_operation_secret_echo",
                    "message": private_marker,
                    "context": {
                        "client_operation_id": CLIENT_OPERATION_ID,
                        "private": private_marker,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="operation or capability material") as caught:
        await adapter(settings, handler).call_tool(
            "append_event", protected_tool_arguments()["append_event"]
        )

    message = str(caught.value)
    assert "changing an argument makes this a new intent" in message
    assert CLIENT_OPERATION_ID not in message
    assert private_marker not in message


async def test_protected_retry_remains_stateless_across_adapter_restart(settings):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "deleted": True,
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "version": 4,
            },
        )

    arguments = protected_tool_arguments()["delete_work"]
    first = structured(
        await adapter(settings, handler).call_tool("delete_work", arguments)
    )
    restarted = structured(
        await adapter(settings, handler).call_tool("delete_work", arguments)
    )

    assert first == restarted
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert bodies[0]["client_operation_id"] == CLIENT_OPERATION_ID


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
async def test_claim_network_failure_requires_exact_same_request_id(settings, tool_name):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout(
            f"private transport error: {API_KEY} {LEASE_TOKEN}", request=request
        )

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            tool_name,
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert "search or recall cannot recover the lease token" in message
    assert CLAIM_REQUEST_ID not in message
    assert LEASE_TOKEN not in message
    assert API_KEY not in message
    assert len(requests) == 1


@pytest.mark.parametrize(
    "status, response_body",
    [
        (500, {"detail": f"private {LEASE_TOKEN} at http://api:8000"}),
        (200, {"work_item_id": WORK_ID, "lease_token": LEASE_TOKEN}),
    ],
)
async def test_ambiguous_claim_response_requires_same_request_id(
    settings, status, response_body
):
    def handler(request):
        return httpx.Response(status, json=response_body)

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            "claim_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert LEASE_TOKEN not in message
    assert "http://api:8000" not in message


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
@pytest.mark.parametrize("code", ["database_unavailable", "claim_request_expired"])
async def test_structured_503_claim_response_requires_same_request_id(
    settings, tool_name, code
):
    private_url = "https://internal.invalid/private/database"

    def handler(request):
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": code,
                    "message": f"private {LEASE_TOKEN} at {private_url}",
                    "context": {
                        "lease_token": LEASE_TOKEN,
                        "private_url": private_url,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match="exact same claim_request_id") as caught:
        await adapter(settings, handler).call_tool(
            tool_name,
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": "claude-code",
                "holder_session_id": "claiming-session",
                "claim_request_id": CLAIM_REQUEST_ID,
            },
        )
    message = str(caught.value)
    assert "claim outcome is unknown" in message
    assert LEASE_TOKEN not in message
    assert private_url not in message


async def test_invalid_project_id_cannot_alter_request_path(settings):
    def handler(request):
        pytest.fail("Path-like project ID must not reach the REST service")

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("recall_work", {
            "project_id": "../other-project", "work_item_id": WORK_ID,
        })


async def test_phase9_core_catalog_exposes_exact_merge_and_search_contracts(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    merge = tools["merge_work"]
    merge_input = merge.inputSchema
    assert set(merge_input["properties"]) == {
        "project_id",
        "source_work_item_id",
        "destination_work_item_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "client_operation_id",
        "merged_by_model",
        "lease_token",
    }
    assert set(merge_input["required"]) == {
        "project_id",
        "source_work_item_id",
        "destination_work_item_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "client_operation_id",
    }
    revision = merge_input["$defs"]["MergeReviewRevision"]
    assert revision["additionalProperties"] is False
    assert set(revision["required"]) == {
        "work_version",
        "context_checkpoint_id",
        "work_event_count",
    }
    token_schema = next(
        option
        for option in merge_input["properties"]["lease_token"]["anyOf"]
        if option.get("type") == "string"
    )
    assert token_schema == {
        "type": "string",
        "format": "password",
        "writeOnly": True,
        "minLength": 1,
        "maxLength": 200,
    }
    assert list(merge.outputSchema["properties"]) == [
        "merge",
        "source_work_item",
        "destination_work_item",
        "direct_destination",
        "canonical_work_item",
        "supporting_relationship_created",
        "supporting_relationship",
        "relationship_events",
        "merge_events",
    ]
    assert list(merge.outputSchema["$defs"]["WorkMergeRead"]["properties"]) == [
        "id",
        "merge_sequence",
        "project_id",
        "source_work_item_id",
        "destination_work_item_id",
        "duplicate_relationship_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "resulting_source_work_version",
        "resulting_destination_work_version",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "merged_by_model",
        "created_at",
    ]
    assert merge.outputSchema["$defs"]["WorkMergedMetadata"]["additionalProperties"] is False

    search = tools["search_work"].inputSchema["properties"]
    assert search["view"]["default"] == "full"
    assert search["view"]["enum"] == ["full", "roots"]
    assert search["duplicate_scope"]["default"] == "canonical"
    assert search["duplicate_scope"]["enum"] == ["canonical", "aliases", "all"]
    assert "minimal" not in json.dumps(tools["search_work"].inputSchema)
    assert set(tools["get_work"].outputSchema["properties"]) == {
        "work_item",
        "canonical",
        "code_review_context",
    }
    assert tools["get_work"].outputSchema["$defs"]["CanonicalWorkProjection"][
        "additionalProperties"
    ] is False


def _duplicate_context(work_context):
    canonical = {
        "id": OTHER_WORK_ID,
        "title": "Canonical retained objective",
        "status": "pending",
    }
    requested = {
        "id": WORK_ID,
        "title": work_context["work_item"]["title"],
        "status": work_context["work_item"]["status"],
    }
    return {
        **work_context,
        "canonical": {
            "is_duplicate": True,
            "direct_destination": canonical,
            "canonical_work_item": canonical,
            "path": [canonical],
            "duplicate_member_count": 1,
        },
        "duplicate_members": [requested],
        "duplicate_member_total": 1,
        "omitted_duplicate_member_count": 0,
        "readiness": {
            **work_context["readiness"],
            "is_duplicate": True,
            "canonical_work_item_id": OTHER_WORK_ID,
            "is_ready": False,
            "display_state": "duplicate",
        },
    }


async def test_exact_alias_get_recall_resource_and_prompt_never_redirect(
    settings, work_context
):
    alias_context = _duplicate_context(work_context)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=alias_context)
        return httpx.Response(
            200,
            json={
                "work_item": work_context["work_item"],
                "canonical": alias_context["canonical"],
            },
        )

    server = adapter(settings, handler)
    detail = structured(
        await server.call_tool(
            "get_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    )
    recalled = structured(
        await server.call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    )
    resources = await server.read_resource(
        f"mnemonic://projects/{PROJECT_ID}/work-items/{WORK_ID}"
    )
    resource = json.loads(next(iter(resources)).content)
    prompt = await server.get_prompt(
        "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    )
    prompt_text = prompt.messages[0].content.text

    for document in (detail, recalled, resource):
        assert document["work_item"]["id"] == WORK_ID
        assert document["canonical"]["canonical_work_item"]["id"] == OTHER_WORK_ID
    assert recalled["initial_checkpoint"]["work_item_id"] == WORK_ID
    resumed = json.loads(prompt_text.split("\n\n", 1)[1])
    assert resumed["initial_checkpoint"]["prompt"] == work_context["initial_checkpoint"][
        "prompt"
    ]
    assert "frozen duplicate audit record" in prompt_text
    assert "silently substitute its canonical ID" in prompt_text
    assert "recall the canonical_work_item ID separately" in prompt_text
    assert calls == [
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context",
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context",
    ]


async def test_alias_audit_search_and_canonical_root_hierarchy_shapes(
    settings, work_summary
):
    alias_summary = {
        **work_summary,
        "readiness": {
            **work_summary["readiness"],
            "is_duplicate": True,
            "canonical_work_item_id": OTHER_WORK_ID,
            "is_ready": False,
            "display_state": "duplicate",
        },
    }
    seen = []

    def handler(request):
        params = dict(request.url.params)
        seen.append(params)
        if params["view"] == "roots":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "summary": work_summary,
                            "self_matches_filter": True,
                            "has_matching_descendants": False,
                            "presentation": {
                                "direct_child_count": 0,
                                "descendant_count": 0,
                                "blocked_descendant_count": 0,
                                "active_descendant_count": 0,
                                "completed_descendant_count": 0,
                                "discovered_descendant_count": 0,
                                "branch_unresolved_human_gate_count": 0,
                                "branch_merged_duplicate_count": 3,
                                "is_discovered_work": False,
                                "discovered_from_parent": False,
                                "next_active_descendant_lease_expires_at": None,
                            },
                        }
                    ],
                    "total": 1,
                    "limit": 30,
                    "offset": 0,
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "summary": alias_summary,
                        "matched_member": {
                            "id": WORK_ID,
                            "title": alias_summary["work_item"]["title"],
                            "status": alias_summary["work_item"]["status"],
                        },
                    }
                ],
                "total": 1,
                "limit": 30,
                "offset": 0,
            },
        )

    server = adapter(settings, handler)
    audit = structured(
        await server.call_tool(
            "search_work",
            {
                "project_id": PROJECT_ID,
                "q": "retained symptom",
                "duplicate_scope": "aliases",
                "canonical_work_item_id": OTHER_WORK_ID,
            },
        )
    )
    roots = structured(
        await server.call_tool(
            "search_work", {"project_id": PROJECT_ID, "view": "roots"}
        )
    )
    assert audit["items"][0]["summary"]["readiness"]["display_state"] == "duplicate"
    assert audit["items"][0]["matched_member"]["id"] == WORK_ID
    assert roots["items"][0]["presentation"]["branch_merged_duplicate_count"] == 3
    assert seen == [
        {
            "q": "retained symptom",
            "status": "pending",
            "view": "full",
            "duplicate_scope": "aliases",
            "canonical_work_item_id": OTHER_WORK_ID,
            "limit": "30",
            "offset": "0",
        },
        {
            "status": "pending",
            "view": "roots",
            "duplicate_scope": "canonical",
            "limit": "30",
            "offset": "0",
        },
    ]


async def test_alias_search_rejects_nonself_matched_member(settings, work_summary):
    alias_summary = {
        **work_summary,
        "readiness": {
            **work_summary["readiness"],
            "is_duplicate": True,
            "canonical_work_item_id": OTHER_WORK_ID,
            "is_ready": False,
            "display_state": "duplicate",
        },
    }

    def handler(request):
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "summary": alias_summary,
                        "matched_member": {
                            "id": OTHER_WORK_ID,
                            "title": "Forged group member",
                            "status": "pending",
                        },
                    }
                ],
                "total": 1,
                "limit": 30,
                "offset": 0,
            },
        )

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool(
            "search_work",
            {
                "project_id": PROJECT_ID,
                "q": "retained symptom",
                "duplicate_scope": "aliases",
                "canonical_work_item_id": OTHER_WORK_ID,
            },
        )


async def test_merge_work_sends_exact_private_intent_once(
    settings, work_item, checkpoint, relationship, progress_event, human_gate
):
    arguments = {**protected_tool_arguments()["merge_work"], "lease_token": LEASE_TOKEN}
    response = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )["merge_work"]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, json=response)

    result = structured(await adapter(settings, handler).call_tool("merge_work", arguments))
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == (
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/merge"
    )
    assert json.loads(requests[0].content) == {
        "client_operation_id": CLIENT_OPERATION_ID,
        "destination_work_item_id": OTHER_WORK_ID,
        "reviewed_source_revision": arguments["reviewed_source_revision"],
        "reviewed_destination_revision": arguments["reviewed_destination_revision"],
        "rationale": arguments["rationale"],
        "merged_by_client": arguments["merged_by_client"],
        "merged_by_session_id": arguments["merged_by_session_id"],
        "merged_by_model": arguments["merged_by_model"],
        "lease_token": LEASE_TOKEN,
    }
    assert LEASE_TOKEN not in json.dumps(result)
    assert CLIENT_OPERATION_ID not in json.dumps(result)


async def test_merge_work_accepts_reused_historical_duplicate_mark(
    settings, work_item, checkpoint, relationship, progress_event, human_gate
):
    response = json.loads(
        json.dumps(
            protected_success_responses(
                work_item, checkpoint, relationship, progress_event, human_gate
            )["merge_work"]
        )
    )
    response["supporting_relationship_created"] = False
    response["relationship_events"] = []
    response["supporting_relationship"].update(
        {
            "context_checkpoint_work_item_id": OTHER_WORK_ID,
            "context_checkpoint_id": OTHER_CHECKPOINT_ID,
            "created_by_client": "historical-client",
            "created_by_session_id": "historical-session",
            "created_by_model": None,
            "created_at": "2026-08-29T12:00:00Z",
        }
    )

    def handler(request):
        return httpx.Response(201, json=response)

    result = structured(
        await adapter(settings, handler).call_tool(
            "merge_work", protected_tool_arguments()["merge_work"]
        )
    )
    assert result["supporting_relationship_created"] is False
    assert result["relationship_events"] == []
    assert result["supporting_relationship"]["created_by_client"] == "historical-client"


@pytest.mark.parametrize(
    "corruption",
    ["private_event_fk", "wrong_relationship_projection", "duplicate_event_id"],
)
async def test_corrupt_merge_success_is_redacted_unknown_outcome(
    settings,
    work_item,
    checkpoint,
    relationship,
    progress_event,
    human_gate,
    corruption,
):
    response = json.loads(
        json.dumps(
            protected_success_responses(
                work_item, checkpoint, relationship, progress_event, human_gate
            )["merge_work"]
        )
    )
    if corruption == "private_event_fk":
        response["merge_events"][0]["metadata"]["work_duplicate_merge_id"] = OTHER_WORK_ID
    elif corruption == "wrong_relationship_projection":
        response["relationship_events"][0]["relationship_direction"] = "incoming"
    else:
        response["merge_events"][1]["id"] = response["merge_events"][0]["id"]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            "merge_work",
            {**protected_tool_arguments()["merge_work"], "lease_token": LEASE_TOKEN},
        )
    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert CLIENT_OPERATION_ID not in message
    assert LEASE_TOKEN not in message
    assert "work_duplicate_merge_id" not in message
    assert len(requests) == 1


def _legacy_duplicate_arguments():
    arguments = protected_tool_arguments()
    create_arguments = {
        **arguments["create_work"],
        "initial_relationships": [
            {
                "type": "duplicate-of",
                "direction": "outgoing",
                "other_work_item_id": OTHER_WORK_ID,
            }
        ],
    }
    add_arguments = {
        **arguments["add_relationship"],
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "relationship_type": "duplicate-of",
    }
    return create_arguments, add_arguments


async def test_legacy_duplicate_receipts_still_dispatch_and_replay_once(
    settings, work_item, checkpoint, relationship, progress_event, human_gate
):
    create_arguments, add_arguments = _legacy_duplicate_arguments()
    responses = protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate
    )
    initial_checkpoint = responses["create_work"]["initial_checkpoint"]
    duplicate_edge = {
        **relationship,
        "relationship_type": "duplicate-of",
        "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_work_item_id": None,
        "context_checkpoint_id": None,
        "created_by_client": initial_checkpoint["source_client"],
        "created_by_session_id": initial_checkpoint["source_session_id"],
        "created_by_model": initial_checkpoint["source_model"],
    }
    create_response = {
        **responses["create_work"],
        "initial_relationships": [duplicate_edge],
    }
    add_response = {
        "relationship": {
            **duplicate_edge,
            "created_by_client": add_arguments["created_by_client"],
            "created_by_session_id": add_arguments["created_by_session_id"],
            "created_by_model": None,
        },
        "created": True,
    }
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/relationships"):
            return httpx.Response(200, json=add_response)
        return httpx.Response(201, json=create_response)

    server = adapter(settings, handler)
    created = structured(await server.call_tool("create_work", create_arguments))
    added = structured(await server.call_tool("add_relationship", add_arguments))
    assert created["initial_relationships"][0]["relationship_type"] == "duplicate-of"
    assert added["relationship"]["relationship_type"] == "duplicate-of"
    assert len(requests) == 2
    assert [json.loads(request.content)["client_operation_id"] for request in requests] == [
        CLIENT_OPERATION_ID,
        CLIENT_OPERATION_ID,
    ]


@pytest.mark.parametrize("tool_name", ["create_work", "add_relationship"])
async def test_fresh_legacy_duplicate_call_reaches_definite_backend_rejection_once(
    settings, tool_name
):
    arguments = dict(
        zip(
            ("create_work", "add_relationship"),
            _legacy_duplicate_arguments(),
            strict=True,
        )
    )[tool_name]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "duplicate_merge_required",
                    "message": f"private {API_KEY}",
                    "context": {"private": LEASE_TOKEN},
                }
            },
        )

    with pytest.raises(ToolError, match="Generic duplicate marks are closed") as caught:
        await adapter(settings, handler).call_tool(tool_name, arguments)
    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME not in message
    assert API_KEY not in message
    assert LEASE_TOKEN not in message
    assert len(requests) == 1


async def test_work_duplicate_error_exposes_only_valid_canonical_id(settings):
    private_marker = "private-alias-diagnostic"

    def handler(request):
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "work_duplicate",
                    "message": private_marker,
                    "context": {
                        "canonical_work_item_id": OTHER_WORK_ID,
                        "source_work_item_id": WORK_ID,
                        "lease_token": LEASE_TOKEN,
                        "private": private_marker,
                    },
                }
            },
        )

    with pytest.raises(ToolError, match=OTHER_WORK_ID) as caught:
        await adapter(settings, handler).call_tool(
            "update_work", protected_tool_arguments()["update_work"]
        )
    message = str(caught.value)
    assert "retained duplicate audit record" in message
    assert WORK_ID not in message
    assert LEASE_TOKEN not in message
    assert private_marker not in message


async def test_safe_read_effect_is_not_inferred_from_post_method(settings):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("private-safe-read-diagnostic", request=request)

    api = MnemonicAPI(settings, httpx.MockTransport(handler))
    with pytest.raises(ToolError, match="API is unavailable") as caught:
        await api.request("POST", "read-only-post", effect=TransportEffect.SAFE_READ)
    assert "outcome is unknown" not in str(caught.value)
    assert "private-safe-read-diagnostic" not in str(caught.value)
    assert len(requests) == 1


@pytest.mark.parametrize("corruption", ["merge_checkpoint", "foreign_event"])
async def test_recall_rejects_blended_or_stale_exact_history(
    settings, work_context, progress_event, corruption
):
    response = json.loads(json.dumps(work_context))
    if corruption == "merge_checkpoint":
        response["merge_review_revision"]["context_checkpoint_id"] = OTHER_CHECKPOINT_ID
    else:
        response["recent_events"] = [{**progress_event, "work_item_id": OTHER_WORK_ID}]
        response["event_total"] = 1
        response["omitted_event_count"] = 0

    def handler(request):
        return httpx.Response(200, json=response)

    with pytest.raises(ToolError, match="unexpected response") as caught:
        await adapter(settings, handler).call_tool(
            "recall_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    assert OTHER_WORK_ID not in str(caught.value)
    assert OTHER_CHECKPOINT_ID not in str(caught.value)


async def test_typed_duplicate_graph_failure_is_a_definitive_protected_stop(settings):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            503,
            json={
                "detail": {
                    "code": "duplicate_graph_invalid",
                    "message": f"private {API_KEY}",
                    "context": {"lease_token": LEASE_TOKEN},
                }
            },
        )

    with pytest.raises(ToolError, match="canonical duplicate graph is invalid") as caught:
        await adapter(settings, handler).call_tool(
            "merge_work", protected_tool_arguments()["merge_work"]
        )

    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME not in message
    assert API_KEY not in message
    assert LEASE_TOKEN not in message
    assert len(requests) == 1


@pytest.mark.parametrize("tool_name", ["get_work", "recall_work"])
@pytest.mark.parametrize("scope_field", ["project_id", "id"])
async def test_exact_work_reads_reject_a_different_response_identity(
    settings, work_item, work_context, tool_name, scope_field
):
    del work_item
    response = json.loads(json.dumps(work_context))
    if tool_name == "get_work":
        response = {
            "work_item": response["work_item"],
            "canonical": response["canonical"],
        }
    returned_work = response["work_item"]
    returned_work[scope_field] = (
        OTHER_CHECKPOINT_ID if scope_field == "project_id" else OTHER_WORK_ID
    )

    def handler(request):
        return httpx.Response(200, json=response)

    with pytest.raises(
        ToolError, match="outside the requested exact scope|unexpected response"
    ) as caught:
        await adapter(settings, handler).call_tool(
            tool_name, {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
        )
    assert OTHER_WORK_ID not in str(caught.value)
    assert OTHER_CHECKPOINT_ID not in str(caught.value)


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
async def test_claim_receipt_identity_mismatch_has_unknown_outcome_guidance(
    settings, claim_receipt, active_work_context, tool_name
):
    poisoned_receipt = {**claim_receipt, "holder_session_id": "different-session"}

    def handler(request):
        response = poisoned_receipt
        if tool_name == "claim_and_recall":
            response = {"lease": poisoned_receipt, "context": active_work_context}
        return httpx.Response(200, json=response)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(
            tool_name,
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "holder_client": claim_receipt["holder_client"],
                "holder_session_id": claim_receipt["holder_session_id"],
                "claim_request_id": claim_receipt["claim_request_id"],
            },
        )
    message = str(caught.value)
    assert UNKNOWN_CLAIM_OUTCOME in message
    assert LEASE_TOKEN not in message


async def test_renew_receipt_token_mismatch_never_claims_a_confirmed_renewal(
    settings, claim_receipt
):
    poisoned_receipt = {**claim_receipt, "lease_token": "b" * 64}

    def handler(request):
        return httpx.Response(200, json=poisoned_receipt)

    with pytest.raises(ToolError, match="incoherent renewal response") as caught:
        await adapter(settings, handler).call_tool(
            "renew_claim",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "lease_token": LEASE_TOKEN,
            },
        )
    message = str(caught.value)
    assert "exact same claim_request_id" not in message
    assert LEASE_TOKEN not in message
    assert "b" * 64 not in message
