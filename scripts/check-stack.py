"""Exercise the live HTTP MCP -> REST -> PostgreSQL path and dashboard proxy.

Run with the MCP project's Python environment. Checks are read-only unless a
project is explicitly authorized with --project-id. The writable check creates
a small, uniquely marked work graph, exercises Advisory duplicate suggestions,
human gates, structured completion evidence, ready discovery, its canonical
event lifecycle, and one real
irreversible duplicate merge with response-loss receipt recovery. Cleanup
removes the reversible graph but deliberately retains immutable completion
evidence plus both merged work items, their frozen relationship, merge record,
events, and receipts as evidence.

The writable Phase 12 path also requires --verified-against plus one or more
--affected-path values. They must describe a real commit and dependency scope
that the operator actually inspected; the checker never fabricates repository
provenance and does not run Git itself.

Never authorize writes against a project without permission. Because merge
evidence cannot be deleted, use --project-id only with a disposable project.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from builtins import BaseExceptionGroup
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from mcp.client.streamable_http import streamablehttp_client
from mnemonic_mcp.models import CheckpointInput
from mnemonic_mcp.phase12_models import (
    JobCompletionReportInput,
    JobCompletionReportRead,
)
from pydantic import AnyUrl, ValidationError

from mcp import ClientSession

CANONICAL_TOOLS = {
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
    "recall_work",
    "request_human_input",
    "list_human_attention",
    "list_work_gates",
    "update_work",
    "complete_work",
    "list_completion_evidence",
    "delete_work",
    "claim_work",
    "claim_and_recall",
    "renew_claim",
    "release_claim",
    "add_relationship",
    "get_relationship",
    "list_relationships",
    "remove_relationship",
    "append_event",
    "list_work_events",
    "merge_work",
    "suggest_duplicate_work",
}
PROTECTED_MUTATION_TOOLS = {
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
EXCLUDED_MUTATION_TOOLS = {
    "create_project",
    "claim_work",
    "claim_and_recall",
    "renew_claim",
}
READ_ONLY_TOOLS = CANONICAL_TOOLS - PROTECTED_MUTATION_TOOLS - EXCLUDED_MUTATION_TOOLS
DESTRUCTIVE_TOOLS = {
    "update_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "merge_work",
}
SYNTHETIC_CLIENT = "mnemonic-stack-check"
SYNTHETIC_GATE_QUESTION = "Choose the synthetic validation decision for this disposable work item."
SYNTHETIC_GATE_RESOLUTION = "Approve the synthetic validation path for this disposable work item."


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _validation_schema(value: Any) -> Any:
    """Drop presentation-only JSON Schema annotations before exact comparison."""
    if isinstance(value, dict):
        return {
            key: _validation_schema(item)
            for key, item in value.items()
            if key not in {"description", "examples", "title"}
        }
    if isinstance(value, list):
        return [_validation_schema(item) for item in value]
    return value


def project_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError("Project IDs must be UUIDs.") from None


def full_commit_oid(value: str) -> str:
    if len(value) not in {40, 64} or any(
        character not in "0123456789abcdefABCDEF" for character in value
    ):
        raise argparse.ArgumentTypeError(
            "Repository baselines must be full 40- or 64-hex commit object IDs."
        )
    return value.lower()


def validated_repository_scope(
    verified_against: str,
    affected_paths: list[str],
) -> tuple[str, list[str]]:
    try:
        checkpoint = CheckpointInput(
            prompt="Validate the explicitly supplied live-stack repository declaration.",
            source_client=SYNTHETIC_CLIENT,
            source_session_id="stack-check-scope-validation",
            verified_against=verified_against,
            affected_paths=affected_paths,
        )
    except ValidationError as error:
        raise argparse.ArgumentTypeError(
            "Repository paths do not satisfy the Phase 10 declaration contract."
        ) from error
    assert checkpoint.verified_against is not None
    return checkpoint.verified_against, list(checkpoint.affected_paths)


def local_settings() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


async def tool(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    require(not result.isError, f"MCP {name} reported an error.")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == "text"))


def synthetic_summary(marker: str) -> str:
    return f"Synthetic Phase 12 integration check {marker}"


def mutation_actor(run_id: str) -> dict[str, str]:
    return {
        "actor_client": SYNTHETIC_CLIENT,
        "actor_session_id": run_id,
    }


def retained_mutation(arguments: dict[str, Any]) -> dict[str, Any]:
    """Prepare one private, exact argument object before dispatching a mutation."""
    require(
        "client_operation_id" not in arguments,
        "A retained mutation cannot replace an existing client operation ID.",
    )
    return {**arguments, "client_operation_id": str(uuid4())}


def require_retained_mutation(arguments: dict[str, Any]) -> None:
    """Fail locally if a protected call lost its retained canonical UUID."""
    value = arguments.get("client_operation_id")
    require(isinstance(value, str), "A protected mutation has no retained operation ID.")
    try:
        require(str(UUID(value)) == value, "A client operation ID is not canonical.")
    except ValueError:
        raise RuntimeError("A client operation ID is not a UUID.") from None


async def protected_tool(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch one protected tool with the caller-retained arguments unchanged."""
    require(name in PROTECTED_MUTATION_TOOLS, f"{name} is not a protected mutation.")
    require_retained_mutation(arguments)
    encoded_arguments = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    result = await tool(session, name, arguments)
    require(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")) == encoded_arguments,
        f"MCP {name} mutated its caller-retained arguments.",
    )
    return result


def _is_target_tool_call(
    payload: object,
    name: str,
    operation_id: str,
) -> bool:
    if isinstance(payload, list):
        return any(_is_target_tool_call(item, name, operation_id) for item in payload)
    if not isinstance(payload, dict) or payload.get("method") != "tools/call":
        return False
    params = payload.get("params")
    if not isinstance(params, dict) or params.get("name") != name:
        return False
    arguments = params.get("arguments")
    return isinstance(arguments, dict) and arguments.get("client_operation_id") == operation_id


class OneShotToolResponseLoss(httpx.AsyncBaseTransport):
    """Drop one completed matching HTTP response before the MCP client sees it."""

    def __init__(self, name: str, operation_id: str) -> None:
        self.name = name
        self.operation_id = operation_id
        self.dropped = False
        self._transport = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = None
        should_drop = not self.dropped and _is_target_tool_call(
            payload, self.name, self.operation_id
        )
        response = await self._transport.handle_async_request(request)
        if should_drop:
            # Reading the complete response proves the server finished the
            # call; raising here gives the caller a genuine unknown outcome.
            await response.aread()
            await response.aclose()
            self.dropped = True
            raise httpx.ReadError(
                "Synthetic one-shot MCP response loss after server completion.",
                request=request,
            )
        return response

    async def aclose(self) -> None:
        await self._transport.aclose()


def _contains_transport_error(error: BaseException) -> bool:
    if isinstance(error, httpx.TransportError):
        return True
    if isinstance(error, BaseExceptionGroup):
        return any(_contains_transport_error(item) for item in error.exceptions)
    return False


async def lose_protected_tool_response(
    mcp_url: str,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, Any],
) -> None:
    """Commit one tool call while making its first response unknowable to the caller."""
    require_retained_mutation(arguments)
    operation_id = arguments["client_operation_id"]
    transport = OneShotToolResponseLoss(name, operation_id)

    def client_factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            transport=transport,
            follow_redirects=True,
        )

    caller_observed_failure = False
    try:
        async with (
            streamablehttp_client(
                mcp_url,
                headers=headers,
                timeout=15,
                httpx_client_factory=client_factory,
            ) as (read, write, _),
            ClientSession(read, write) as loss_session,
        ):
            await loss_session.initialize()
            await loss_session.call_tool(name, arguments)
    except httpx.TransportError:
        caller_observed_failure = True
    except BaseExceptionGroup as error:
        if not _contains_transport_error(error):
            raise
        caller_observed_failure = True
    require(transport.dropped, "The synthetic MCP response-loss boundary did not fire.")
    require(
        caller_observed_failure,
        "The synthetic MCP caller unexpectedly observed the discarded response.",
    )


async def retained_api_mutation(
    api: httpx.AsyncClient,
    method: str,
    path: str,
    arguments: dict[str, Any],
) -> httpx.Response:
    """Keep one cleanup request byte-equivalent through one ambiguous retry."""
    require_retained_mutation(arguments)
    frozen = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    ambiguous_statuses = {408, 425, 429, 500, 502, 503, 504}
    for attempt in range(2):
        try:
            response = await api.request(method, path, json=arguments)
        except httpx.TransportError:
            if attempt == 0:
                continue
            raise RuntimeError("Cleanup remained unresolved after an exact retry.") from None
        require(
            json.dumps(arguments, sort_keys=True, separators=(",", ":")) == frozen,
            "A retained cleanup mutation changed before resolution.",
        )
        if response.status_code in ambiguous_statuses and attempt == 0:
            continue
        return response
    raise RuntimeError("Cleanup remained unresolved after an exact retry.")


async def work_events(session: ClientSession, identity: dict[str, str]) -> list[dict[str, Any]]:
    page = await tool(
        session,
        "list_work_events",
        {**identity, "order": "oldest", "limit": 100, "offset": 0},
    )
    require(
        page["total"] == len(page["items"]),
        "Synthetic event history unexpectedly exceeded one bounded page.",
    )
    return page["items"]


async def require_event_types(
    session: ClientSession,
    identity: dict[str, str],
    expected: list[str],
) -> list[dict[str, Any]]:
    events = await work_events(session, identity)
    require(
        [event["event_type"] for event in events] == expected,
        f"Unexpected synthetic event timeline; expected {expected!r}.",
    )
    require(
        [(event["created_at"], event["id"]) for event in events]
        == sorted((event["created_at"], event["id"]) for event in events),
        "Synthetic events were not returned in deterministic oldest-first order.",
    )
    return events


def ready_ids(page: dict[str, Any]) -> list[str]:
    for item in page["items"]:
        require(
            item.get("display_state") in {"pending", "dropped"}
            and "summary" not in item.get("work_item", {})
            and "current_context" not in item,
            "Ready discovery widened beyond the compact pointer contract.",
        )
    return [item["work_item"]["id"] for item in page["items"]]


def require_completion_evidence(
    completion: dict[str, Any],
    expected: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Require exact child semantics plus server-owned episode identity."""
    evidence = completion.get("completion_evidence")
    require(isinstance(evidence, dict), "Completion omitted its structured evidence.")
    checkpoint = completion["checkpoint"]
    work_item = completion["work_item"]
    for family in ("verification_results", "artifact_references"):
        actual_items = evidence.get(family)
        expected_items = expected[family]
        require(
            isinstance(actual_items, list) and len(actual_items) == len(expected_items),
            f"Completion returned the wrong {family} count.",
        )
        for position, (actual, requested) in enumerate(
            zip(actual_items, expected_items, strict=True)
        ):
            require(
                isinstance(actual, dict)
                and all(actual.get(key) == value for key, value in requested.items())
                and actual.get("work_item_id") == work_item["id"]
                and actual.get("completion_checkpoint_id") == checkpoint["id"]
                and actual.get("position") == position
                and actual.get("created_at") == checkpoint["created_at"]
                and isinstance(actual.get("id"), str),
                f"Completion returned incoherent {family} identity or content.",
            )
    return evidence


def work_detail_parts(
    value: object,
    expected_work_item_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require and unpack the exact Phase 10 detail envelope."""
    if not isinstance(value, dict):
        raise TypeError("Work detail was not an object.")
    detail: dict[str, Any] = value
    require(
        set(detail) == {"work_item", "canonical"},
        "Work detail did not use the exact work_item/canonical envelope.",
    )
    work_item = detail["work_item"]
    canonical = detail["canonical"]
    require(
        isinstance(work_item, dict)
        and isinstance(canonical, dict)
        and set(canonical)
        == {
            "is_duplicate",
            "direct_destination",
            "canonical_work_item",
            "path",
            "duplicate_member_count",
        }
        and isinstance(canonical.get("canonical_work_item"), dict),
        "Work detail returned an invalid canonical projection.",
    )
    if expected_work_item_id is not None:
        require(
            work_item.get("id") == expected_work_item_id,
            "Work detail silently substituted a different identity.",
        )
    if canonical["is_duplicate"]:
        require(
            canonical["direct_destination"] is not None
            and canonical["path"]
            and canonical["path"][-1] == canonical["canonical_work_item"],
            "Duplicate work detail returned an incoherent destination path.",
        )
    else:
        require(
            canonical["direct_destination"] is None
            and canonical["path"] == []
            and canonical["canonical_work_item"].get("id") == work_item.get("id"),
            "Canonical work detail did not point to its exact requested identity.",
        )
    return work_item, canonical


def textual_resource_json(value: object) -> dict[str, Any]:
    """Require and decode a textual JSON MCP resource."""
    text = getattr(value, "text", None)
    if not isinstance(text, str):
        raise TypeError("Canonical MCP resource was not textual JSON.")
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise TypeError("Canonical MCP resource JSON was not an object.")
    return decoded


async def merge_observables(
    session: ClientSession,
    source_identity: dict[str, str],
    destination_identity: dict[str, str],
) -> dict[str, Any]:
    """Capture public state that an exact merge receipt replay must not change."""
    source_detail = await tool(session, "get_work", source_identity)
    destination_detail = await tool(session, "get_work", destination_identity)
    work_detail_parts(source_detail, source_identity["work_item_id"])
    work_detail_parts(destination_detail, destination_identity["work_item_id"])
    return {
        "source_detail": source_detail,
        "destination_detail": destination_detail,
        "source_context": await tool(session, "recall_work", source_identity),
        "destination_context": await tool(session, "recall_work", destination_identity),
        "source_events": await work_events(session, source_identity),
        "destination_events": await work_events(session, destination_identity),
        "source_relationships": await tool(
            session,
            "list_relationships",
            {**source_identity, "direction": "both", "limit": 100, "offset": 0},
        ),
        "destination_relationships": await tool(
            session,
            "list_relationships",
            {
                **destination_identity,
                "direction": "both",
                "limit": 100,
                "offset": 0,
            },
        ),
    }


def typed_error_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("detail", {}).get("code")
    except (json.JSONDecodeError, AttributeError):
        return None


async def find_synthetic_work(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    run_id: str,
) -> list[str]:
    """Find this run's exact synthetic graph after an uncertain create response."""
    response = await api.get(
        f"projects/{project_id}/work-items",
        params={
            "q": marker,
            "status": "all",
            "view": "full",
            "duplicate_scope": "all",
            "limit": 100,
            "offset": 0,
        },
    )
    require(response.status_code == 200, "Could not inspect synthetic work for cleanup.")
    matches: list[str] = []
    for item in response.json()["items"]:
        require(
            isinstance(item, dict)
            and set(item) == {"summary", "matched_member"}
            and isinstance(item.get("summary"), dict)
            and isinstance(item.get("matched_member"), dict),
            "Cleanup search did not return a Phase 10 full-search hit.",
        )
        summary = item["summary"]
        work_item = summary.get("work_item", {})
        current_context = summary.get("current_context", {})
        if (
            work_item.get("summary") == synthetic_summary(marker)
            and current_context.get("source_client") == SYNTHETIC_CLIENT
            and current_context.get("source_session_id") == run_id
        ):
            require(
                item["matched_member"].get("id") == work_item.get("id"),
                "All-scope cleanup search confused match evidence with its returned row.",
            )
            matches.append(work_item["id"])
    require(
        len(matches) <= 7,
        "Cleanup found more synthetic records than this lifecycle can create.",
    )
    return matches


async def find_synthetic_relationships(
    api: httpx.AsyncClient,
    project_id: str,
    work_item_ids: set[str],
    run_id: str,
) -> set[str]:
    """Discover removable run-owned edges, never frozen merge evidence."""
    relationship_ids: set[str] = set()
    for work_item_id in work_item_ids:
        response = await api.get(
            f"projects/{project_id}/work-items/{work_item_id}/relationships",
            params={"direction": "both", "limit": 100, "offset": 0},
        )
        require(
            response.status_code == 200,
            "Could not inspect synthetic relationships for cleanup.",
        )
        for adjacent in response.json()["items"]:
            edge = adjacent.get("relationship", {})
            endpoints = {
                edge.get("source_work_item_id"),
                edge.get("target_work_item_id"),
            }
            if (
                edge.get("relationship_type") != "duplicate-of"
                and edge.get("created_by_client") == SYNTHETIC_CLIENT
                and edge.get("created_by_session_id") == run_id
                and endpoints <= work_item_ids
            ):
                relationship_ids.add(edge["id"])
    return relationship_ids


def require_synthetic_relationship(
    edge: dict[str, Any], work_item_ids: set[str], run_id: str
) -> None:
    endpoints = {
        edge.get("source_work_item_id"),
        edge.get("target_work_item_id"),
    }
    require(
        edge.get("relationship_type") != "duplicate-of"
        and edge.get("created_by_client") == SYNTHETIC_CLIENT
        and edge.get("created_by_session_id") == run_id
        and endpoints <= work_item_ids,
        "Refusing to remove frozen merge evidence or a relationship outside this run.",
    )


async def resolve_synthetic_gates_for_cleanup(
    api: httpx.AsyncClient,
    project_id: str,
    work_item_id: str,
    run_id: str,
) -> None:
    """Resolve only this run's gates so interrupted synthetic work can be deleted."""
    path = f"projects/{project_id}/work-items/{work_item_id}/gates"
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"status": "unresolved", "limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = await api.get(path, params=params)
        require(
            response.status_code == 200,
            "Could not inspect synthetic human gates for cleanup.",
        )
        page = response.json()
        for gate in page["items"]:
            if (
                gate.get("question") != SYNTHETIC_GATE_QUESTION
                or gate.get("requested_by_client") != SYNTHETIC_CLIENT
                or gate.get("requested_by_session_id") != run_id
            ):
                continue
            arguments: dict[str, Any] = {
                "resolution": SYNTHETIC_GATE_RESOLUTION,
                "resolved_by_client": SYNTHETIC_CLIENT,
                "resolved_by_session_id": run_id,
                "resolved_by_model": None,
                "reviewed_context_revision": gate["current_context_revision"],
            }
            resolution_arguments = retained_mutation(arguments)
            resolved = await retained_api_mutation(
                api,
                "POST",
                path + f"/{gate['id']}/resolve",
                resolution_arguments,
            )
            require(
                resolved.status_code == 200
                and resolved.json().get("id") == gate["id"]
                and resolved.json().get("status") == "resolved",
                "Synthetic human-gate cleanup failed.",
            )
        cursor = page.get("next_cursor")
        if cursor is None:
            break


async def preserved_merge_work_ids(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    work_item_ids: set[str],
    known_work_item_ids: set[str],
) -> set[str]:
    """Validate the run inventory and identify immutable merged groups."""
    for known_work_item_id in known_work_item_ids:
        response = await api.get(f"projects/{project_id}/work-items/{known_work_item_id}")
        if response.status_code == 404:
            continue
        require(
            response.status_code == 200 and known_work_item_id in work_item_ids,
            "Refusing to clean up a known ID that was not proven to belong to this run.",
        )
        work_detail_parts(response.json(), known_work_item_id)

    preserved: set[str] = set()
    for work_item_id in sorted(work_item_ids):
        response = await api.get(f"projects/{project_id}/work-items/{work_item_id}")
        require(
            response.status_code == 200,
            "Could not inspect synthetic work for merge-preserving cleanup.",
        )
        work_item, canonical = work_detail_parts(response.json(), work_item_id)
        require(
            work_item.get("summary") == synthetic_summary(marker),
            "Refusing to clean up work without this run's exact synthetic summary.",
        )
        if canonical["is_duplicate"] or canonical["duplicate_member_count"] > 0:
            root_id = canonical["canonical_work_item"]["id"]
            require(
                root_id in work_item_ids,
                "Synthetic work was merged into a non-synthetic group; refusing cleanup.",
            )
            preserved.update({work_item_id, root_id})
    return preserved


async def remove_synthetic_relationships(
    api: httpx.AsyncClient,
    project_id: str,
    run_id: str,
    work_item_ids: set[str],
    known_relationship_ids: set[str],
) -> None:
    """Remove run-owned reversible edges while refusing frozen duplicate evidence."""
    relationship_ids = await find_synthetic_relationships(api, project_id, work_item_ids, run_id)
    relationship_ids.update(known_relationship_ids)
    for relationship_id in sorted(relationship_ids):
        relationship_path = f"projects/{project_id}/relationships/{relationship_id}"
        response = await api.get(relationship_path)
        if response.status_code == 404:
            continue
        require(
            response.status_code == 200,
            "Could not inspect a known synthetic relationship for cleanup.",
        )
        require_synthetic_relationship(response.json(), work_item_ids, run_id)
        removed = await retained_api_mutation(
            api,
            "DELETE",
            relationship_path,
            retained_mutation({"actor": mutation_actor(run_id)}),
        )
        require(
            removed.status_code == 200
            and removed.json().get("relationship_id") == relationship_id
            and removed.json().get("removed") is True,
            "Synthetic relationship cleanup failed.",
        )


async def cleanup_synthetic_item(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    run_id: str,
    work_item_id: str,
    claim_request_ids: dict[str, str],
    lease_tokens: dict[str, str],
) -> None:
    """Release and soft-delete one proven reversible synthetic item."""
    await resolve_synthetic_gates_for_cleanup(api, project_id, work_item_id, run_id)
    path = f"projects/{project_id}/work-items/{work_item_id}"
    remaining = await api.get(path)
    if remaining.status_code == 404:
        return
    require(
        remaining.status_code == 200,
        "Could not inspect temporary work for cleanup.",
    )
    record, _canonical = work_detail_parts(remaining.json(), work_item_id)
    require(
        record.get("summary") == synthetic_summary(marker),
        "Refusing to clean up work without this run's exact synthetic summary.",
    )
    cleanup_token = lease_tokens.get(work_item_id)
    claim_request_id = claim_request_ids.get(work_item_id)
    if cleanup_token is None and claim_request_id is not None and record["status"] == "pending":
        recovered = await api.post(
            path + "/claim",
            json={
                "holder_client": SYNTHETIC_CLIENT,
                "holder_session_id": run_id,
                "claim_request_id": claim_request_id,
            },
        )
        if recovered.status_code == 200:
            cleanup_token = recovered.json()["lease_token"]
        else:
            require(
                recovered.status_code == 409
                and typed_error_code(recovered) == "claim_request_expired",
                "Could not recover the synthetic lease for cleanup.",
            )
    if cleanup_token is not None and record["status"] == "pending":
        released = await retained_api_mutation(
            api,
            "POST",
            path + "/release-claim",
            retained_mutation(
                {
                    "lease_token": cleanup_token,
                    "actor": mutation_actor(run_id),
                }
            ),
        )
        require(
            released.status_code == 200
            or (released.status_code == 409 and typed_error_code(released) == "lease_expired"),
            "Could not release the synthetic lease for cleanup.",
        )
    remaining = await api.get(path)
    if remaining.status_code == 404:
        return
    require(remaining.status_code == 200, "Could not refresh work for cleanup.")
    record, _canonical = work_detail_parts(remaining.json(), work_item_id)
    cleanup = await retained_api_mutation(
        api,
        "POST",
        path + "/delete",
        retained_mutation(
            {
                "expected_version": record["version"],
                "actor": mutation_actor(run_id),
            }
        ),
    )
    require(
        cleanup.status_code == 200 and cleanup.json().get("deleted") is True,
        "Temporary work cleanup failed.",
    )


async def cleanup_synthetic_work(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    run_id: str,
    known_work_item_ids: set[str],
    known_relationship_ids: set[str],
    claim_request_ids: dict[str, str],
    lease_tokens: dict[str, str],
) -> set[str]:
    """Remove reversible run data and return intentionally retained merge work IDs."""
    work_item_ids = set(await find_synthetic_work(api, project_id, marker, run_id))
    preserved_work_item_ids = await preserved_merge_work_ids(
        api, project_id, marker, work_item_ids, known_work_item_ids
    )

    await remove_synthetic_relationships(
        api, project_id, run_id, work_item_ids, known_relationship_ids
    )

    for work_item_id in sorted(work_item_ids - preserved_work_item_ids, reverse=True):
        await cleanup_synthetic_item(
            api,
            project_id,
            marker,
            run_id,
            work_item_id,
            claim_request_ids,
            lease_tokens,
        )
    return preserved_work_item_ids


def validate_phase12_rest_contract(document: dict[str, Any]) -> None:
    schemas = document["components"]["schemas"]
    prefix = "/api/v1/projects/{project_id}"
    reads = {
        "/activity": "ProjectActivityPage", "/settings": "ProjectSettingsRead",
        "/job-completion-reports": "JobCompletionReportPage",
        "/job-completion-reports/count": "JobCompletionReportCount",
        "/job-completion-reports/{report_id}": "JobCompletionReportDetailEnvelope",
    }
    for suffix, name in reads.items():
        response = document["paths"][prefix + suffix]["get"]["responses"]["200"]
        require(
            response["content"]["application/json"]["schema"]
            == {"$ref": f"#/components/schemas/{name}"},
            f"REST Phase 12 {suffix} does not expose its exact safe-read shape.",
        )
    for name in ("WorkCompletionCreate", "WorkItemPatch", "WorkCompletionRead", "WorkUpdateRead"):
        schema = schemas[name]
        require(
            "job_completion_report" in schema["properties"]
            and "job_completion_report" not in schema.get("required", []),
            f"REST {name} lost sparse historical report replay.",
        )
    require(
        set(schemas["JobCompletionReportInput"]["required"])
        == {"summary", "fyi_items", "prompt_revision"}
        and "job_completion_report" not in schemas["WorkItemRead"]["properties"]
        and "job_completion_report_prompt" in schemas["ProjectSettingsRead"]["required"]
        and "revision" in schemas["ProjectSettingsRead"]["required"],
        "REST Phase 12 report/settings contracts are incomplete.",
    )


def validate_phase12_mcp_catalog(tools: dict[str, Any]) -> None:
    for name in ("complete_work", "update_work"):
        schema = tools[name].inputSchema
        require(
            "job_completion_report" in schema["properties"]
            and "job_completion_report" not in schema["required"]
            and set(schema["$defs"]["JobCompletionReportInput"]["required"])
            == {"summary", "fyi_items", "prompt_revision"}
            and "job_completion_report" in tools[name].outputSchema["properties"],
            f"MCP {name} lacks its sparse report input and exact response contract.",
        )
    for name, expected in {
        "get_activity": "ProjectActivityPage",
        "get_project_settings": "ProjectSettingsRead",
        "list_job_completion_reports": "JobCompletionReportPage",
        "get_job_completion_report": "JobCompletionReportDetailEnvelope",
    }.items():
        require(
            tools[name].outputSchema.get("title") == expected,
            f"MCP {name} has an unexpected Phase 12 safe-read projection.",
        )


async def synthetic_job_report(
    session: ClientSession, project_id: str, summary: str, fyi_items: list[str] | None = None
) -> dict[str, Any]:
    """Bind deliberately authored synthetic prose to the prompt just obtained."""
    settings = await tool(session, "get_project_settings", {"project_id": project_id})
    require(
        settings["project_id"] == project_id and bool(settings["job_completion_report_prompt"].strip()),
        "The project has no effective report authoring prompt.",
    )
    return JobCompletionReportInput(
        summary=summary, fyi_items=[] if fyi_items is None else fyi_items,
        prompt_revision=settings["revision"],
    ).model_dump(mode="json")


def require_job_report(
    result: dict[str, Any], requested: dict[str, Any], work_item_id: str, outcome: str
) -> dict[str, Any]:
    actual = result.get("job_completion_report")
    require(isinstance(actual, dict), "Fresh closeout omitted its human-facing report.")
    report = JobCompletionReportRead.model_validate_json(json.dumps(actual), strict=True)
    work = result.get("work_item", result)
    require(
        str(report.work_item_id) == work_item_id
        and str(report.project_id) == work["project_id"]
        and report.closeout_status == outcome and report.closeout_work_version == work["version"]
        and report.model_dump(mode="json", include={"summary", "fyi_items", "prompt_revision"})
        == requested,
        "Closeout report ownership, outcome, version or authored text disagrees.",
    )
    return actual


async def phase12_read_probe(session: ClientSession, project_id: str) -> str:
    settings = await tool(session, "get_project_settings", {"project_id": project_id})
    require(bool(settings["job_completion_report_prompt"].strip()), "Report prompt is blank.")
    initial = await tool(session, "get_activity", {"project_id": project_id, "start": "now"})
    require(initial["items"] == [] and not initial["has_more"], "Activity now bootstrap failed.")
    reports = await tool(session, "list_job_completion_reports", {"project_id": project_id, "limit": 1})
    if reports["items"]:
        await tool(session, "get_job_completion_report", {
            "project_id": project_id, "report_id": reports["items"][0]["report"]["id"],
        })
    return initial["next_cursor"]


async def phase12_dismiss_report(
    api: httpx.AsyncClient, project_id: str, report_id: str, run_id: str
) -> None:
    path = f"projects/{project_id}/job-completion-reports/{report_id}/dismiss"
    actor = {"actor_client": "dashboard", "actor_session_id": run_id, "actor_model": None}
    arguments = retained_mutation({"actor": actor})
    first = await retained_api_mutation(api, "POST", path, arguments)
    repeated = await retained_api_mutation(api, "POST", path, arguments)
    require(first.status_code == 200 and first.json()["dismissed"] is True, "First report dismissal failed.")
    require(first.content == repeated.content, "Dismissal replay changed its permanent result.")
    again = await retained_api_mutation(api, "POST", path, retained_mutation({"actor": actor}))
    require(
        again.status_code == 200 and again.json()["dismissed"] is False
        and again.json()["human_dismissal"] == first.json()["human_dismissal"],
        "A deliberate second dismissal did not preserve the first human action.",
    )


async def phase12_follow_up_action(
    api: httpx.AsyncClient, project_id: str, report_id: str, source_work_item_id: str,
    run_id: str, checkpoint_input: dict[str, Any], known_work_item_ids: set[str],
) -> dict[str, Any]:
    arguments = retained_mutation({
        "actor": {"actor_client": "dashboard", "actor_session_id": run_id, "actor_model": None},
        "title": "Use Comic Sans for the synthetic font choice",
        "summary": "Disposable report follow-up exercising the Arial to Comic Sans override.",
        "priority": 0,
        "initial_checkpoint": {
            **checkpoint_input, "source_client": "dashboard", "source_session_id": run_id,
            "source_model": None,
            "prompt": "Change the synthetic font preference from Arial to Comic Sans. This "
            "is a disposable acceptance objective and does not change the product by itself.",
        },
    })
    path = f"projects/{project_id}/job-completion-reports/{report_id}/follow-ups"
    created = await retained_api_mutation(api, "POST", path, arguments)
    require(created.status_code == 201, "Manual report follow-up creation failed.")
    result = created.json()
    work = result["work_item"]
    known_work_item_ids.add(work["id"])
    follow_up = result["follow_up"]
    require(
        work["status"] == "pending" and follow_up["report_id"] == report_id
        and follow_up["source_work_item_id"] == source_work_item_id
        and follow_up["follow_up_work_item_id"] == work["id"]
        and work["id"] != source_work_item_id,
        "Report follow-up lost pending state or one of its immutable source links.",
    )
    replayed = await retained_api_mutation(api, "POST", path, arguments)
    require(replayed.content == created.content, "Follow-up replay created or returned different work.")
    return result


async def phase12_activity_catch_up(
    session: ClientSession, project_id: str, after: str, report_id: str, follow_up_id: str
) -> None:
    collected: list[dict[str, Any]] = []
    stream_id = None
    for _ in range(10):
        page = await tool(session, "get_activity", {"project_id": project_id, "after": after, "limit": 100})
        if stream_id is None:
            stream_id = page["stream_id"]
        require(page["stream_id"] == stream_id, "Project stream changed during the synthetic run.")
        collected.extend(page["items"])
        after = page["next_cursor"]
        if not page["has_more"]:
            break
    else:
        raise RuntimeError("Synthetic activity exceeded ten bounded pages.")
    sequences = [int(item["sequence"]) for item in collected]
    require(sequences == sorted(set(sequences)), "Activity resume repeated or reordered entries.")
    for kind in ("job_completion_report_created", "job_completion_report_dismissed"):
        require(
            sum(item["kind"] == kind and item["job_completion_report_id"] == report_id for item in collected) == 1,
            f"Activity did not map {kind} exactly once.",
        )
    require(
        sum(item["follow_up_id"] == follow_up_id for item in collected) == 1,
        "Activity replay duplicated or omitted follow-up provenance.",
    )


async def phase12_human_report_flow(
    session: ClientSession, api: httpx.AsyncClient, project_id: str,
    source_identity: dict[str, str], run_id: str, checkpoint_input: dict[str, Any],
    known_work_item_ids: set[str], activity_start: str,
) -> None:
    detail = await tool(session, "get_work", source_identity)
    source, _ = work_detail_parts(detail, source_identity["work_item_id"])
    requested = await synthetic_job_report(
        session, project_id,
        "The disposable verification task was marked Promoted to test a recorded hand-off. "
        "This does not create an external issue, assign an agent, or imply product work finished.",
        ["This synthetic report uses Arial as a sample choice; a follow-up can request Comic Sans."],
    )
    closed = await protected_tool(session, "update_work", retained_mutation({
        **source_identity, "expected_version": source["version"], "changes": {"status": "promoted"},
        **mutation_actor(run_id), "job_completion_report": requested,
    }))
    report = require_job_report(closed, requested, source["id"], "promoted")
    report_id = report["id"]
    reports = await tool(session, "list_job_completion_reports", {**source_identity, "limit": 50})
    require(any(item["report"]["id"] == report_id for item in reports["items"]), "New report is absent from Summaries.")
    follow_up = await phase12_follow_up_action(
        api, project_id, report_id, source["id"], run_id, checkpoint_input, known_work_item_ids
    )
    current = await tool(session, "get_job_completion_report", {"project_id": project_id, "report_id": report_id})
    require(not current["human_dismissed"] and current["follow_up_count"] == "1", "Follow-up silently dismissed or miscounted its report.")
    await phase12_dismiss_report(api, project_id, report_id, run_id)
    current = await tool(session, "get_job_completion_report", {"project_id": project_id, "report_id": report_id})
    require(current["human_dismissed"] and current["report"]["summary"] == requested["summary"], "Dismissal lost immutable report text.")
    reports = await tool(session, "list_job_completion_reports", {**source_identity, "limit": 50})
    require(all(item["report"]["id"] != report_id for item in reports["items"]), "Dismissed report remains in the inbox.")
    await phase12_activity_catch_up(session, project_id, activity_start, report_id, follow_up["follow_up"]["id"])
    new_work = follow_up["work_item"]
    await protected_tool(session, "delete_work", retained_mutation({
        "project_id": project_id, "work_item_id": new_work["id"],
        "expected_version": new_work["version"], **mutation_actor(run_id),
    }))


def validate_rest_contract(document: Any) -> None:
    """Reject a healthy but contract-incompatible pre-Phase-12 API."""
    try:
        require(document["info"]["version"] == "0.18.1", "Unexpected REST API version.")
        schemas = document["components"]["schemas"]
        require(
            {"ExternalReference", "ExternalReferencesChange", "ExternalDuplicateCandidate",
             "ExternalCandidateReference", "ExternalDuplicateSuggestion"}.issubset(schemas)
            and "external_references" in schemas["WorkItemRead"]["properties"]
            and "external_references" in schemas["WorkItemCreate"]["properties"]
            and "external_candidates" in schemas["DuplicateSuggestionRequest"]["properties"]
            and "project_id" in schemas["WorkPointer"]["required"]
            and {"external_items", "external_candidate_count", "external_scope"}.issubset(
                schemas["DuplicateSuggestionPage"]["properties"]
            ),
            "REST external records lack coordinated canonical contracts.",
        )
        endpoint_refs = {
            "/api/v1/projects/{project_id}/work-items": "#/components/schemas/WorkItemCreate",
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints": (
                "#/components/schemas/CheckpointCreate"
            ),
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/complete": (
                "#/components/schemas/WorkCompletionCreate"
            ),
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/move": (
                "#/components/schemas/WorkMoveCreate"
            ),
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/activate": (
                "#/components/schemas/DashboardWorkActivationCreate"
            ),
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/return-to-pending": (
                "#/components/schemas/DashboardWorkPendingCreate"
            ),
        }
        for path, expected_ref in endpoint_refs.items():
            request_schema = document["paths"][path]["post"]["requestBody"]["content"][
                "application/json"
            ]["schema"]
            require(
                request_schema == {"$ref": expected_ref},
                f"REST {path} does not expose the expected Phase 12 request.",
            )
        response_refs = {
            ("/api/v1/projects/{project_id}/work-items", "201"): (
                "#/components/schemas/WorkCreation"
            ),
            (
                "/api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints",
                "201",
            ): "#/components/schemas/CheckpointRead",
            (
                "/api/v1/projects/{project_id}/work-items/{work_item_id}/complete",
                "200",
            ): "#/components/schemas/WorkCompletionRead",
            (
                "/api/v1/projects/{project_id}/work-items/{work_item_id}/move",
                "200",
            ): "#/components/schemas/WorkMoveRead",
            (
                "/api/v1/projects/{project_id}/work-items/{work_item_id}/activate",
                "200",
            ): "#/components/schemas/LeasePublic",
            (
                "/api/v1/projects/{project_id}/work-items/{work_item_id}/return-to-pending",
                "200",
            ): "#/components/schemas/ReleaseResult",
        }
        for (path, status), expected_ref in response_refs.items():
            response_schema = document["paths"][path]["post"]["responses"][status][
                "content"
            ]["application/json"]["schema"]
            require(
                response_schema == {"$ref": expected_ref},
                f"REST {path} does not expose the expected Phase 12 response.",
            )
        lease_public = schemas["LeasePublic"]
        lease_public_fields = {
            "holder_client",
            "holder_session_id",
            "acquired_at",
            "renewed_at",
            "expires_at",
        }
        require(
            lease_public.get("additionalProperties") is False
            and set(lease_public["properties"]) == lease_public_fields | {
                "purpose", "code_review_id", "mode",
            }
            and set(lease_public["required"]) == lease_public_fields,
            "REST manual activation does not return the exact token-free lease projection.",
        )
        require(
            "lease_token" not in schemas["DashboardWorkPendingCreate"]["properties"],
            "REST manual Pending unexpectedly accepts a lease token.",
        )
        require(
            schemas["WorkItemCreate"]["properties"]["initial_checkpoint"]
            == {"$ref": "#/components/schemas/InitialCheckpointCreate"},
            "REST create-work does not bind the Phase 10 checkpoint schema.",
        )
        require(
            schemas["WorkCompletionCreate"]["properties"]["checkpoint"]
            == {"$ref": "#/components/schemas/CompletionCheckpointCreate"},
            "REST complete-work does not bind the Phase 12 checkpoint schema.",
        )
        completion_create = schemas["WorkCompletionCreate"]
        require(
            completion_create.get("if", {}).get("required") == ["completion_evidence"]
            and completion_create.get("then", {}).get("required")
            == ["client_operation_id"]
            and "completion_evidence" in completion_create["properties"],
            "REST completion evidence lacks its executable operation-ID condition.",
        )
        evidence_path = (
            "/api/v1/projects/{project_id}/work-items/{work_item_id}/completion-evidence"
        )
        require(
            set(document["paths"][evidence_path]) == {"get"}
            and document["paths"][evidence_path]["get"]["responses"]["200"]["content"]
            ["application/json"]["schema"]
            == {"$ref": "#/components/schemas/CompletionEvidencePage"},
            "REST completion-evidence history is not the sole safe GET contract.",
        )
        completion_read = schemas["WorkCompletionRead"]
        require(
            "completion_evidence" in completion_read["properties"]
            and "completion_evidence" not in completion_read.get("required", [])
            and {
                "ArtifactReferenceRead",
                "CommandVerificationRead",
                "CompletionEvidenceEpisodeRead",
                "CompletionEvidencePage",
                "ObservationVerificationRead",
            }.issubset(schemas),
            "REST completion evidence lacks its strict write/read schemas.",
        )
        public_schema = json.dumps(schemas, sort_keys=True)
        require(
            '"completion_generation"' not in public_schema
            and '"reopen_generation"' not in public_schema,
            "REST OpenAPI exposes private completion-generation fields.",
        )
        expected_scope_schema = {
            "items": {
                "maxLength": 512,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9._@+=,~*/-]+$",
                "type": "string",
            },
            "maxItems": 64,
            "type": "array",
        }
        expected_input_commit_schema = {
            "anyOf": [
                {
                    "pattern": "^[0-9a-fA-F]{7,64}$",
                    "type": "string",
                },
                {"type": "null"},
            ]
        }
        for name in (
            "InitialCheckpointCreate",
            "CheckpointCreate",
            "CompletionCheckpointCreate",
        ):
            schema = schemas[name]
            affected_paths = _validation_schema(
                schema["properties"]["affected_paths"]
            )
            verified_against = _validation_schema(
                schema["properties"]["verified_against"]
            )
            require(
                schema["additionalProperties"] is False
                and affected_paths == expected_scope_schema
                and verified_against == expected_input_commit_schema,
                f"REST {name} lacks the exact Phase 10 repository declaration.",
            )
            require(
                {"verified_against", "affected_paths"}.isdisjoint(
                    schema.get("required", [])
                ),
                f"REST {name} incorrectly requires repository provenance.",
            )
        validate_phase12_rest_contract(document)
        checkpoint_read = schemas["CheckpointRead"]
        require(
            _validation_schema(checkpoint_read["properties"]["affected_paths"])
            == expected_scope_schema
            and "affected_paths" not in checkpoint_read.get("required", []),
            "REST full checkpoint reads lack optional Phase 10 scope.",
        )
        require(
            "affected_paths" not in schemas["CheckpointPointer"]["properties"],
            "REST compact checkpoint pointers expose repository scope.",
        )
    except (KeyError, TypeError) as error:
        raise RuntimeError("REST OpenAPI is missing the Phase 12 contract.") from error


def validate_mcp_catalog(catalog: Any) -> None:
    """Require the exact tool set, annotations, and operation-ID boundaries."""
    tools_by_name = {entry.name: entry for entry in catalog.tools}
    require(
        len(catalog.tools) == 38
        and len(tools_by_name) == 38
        and len(PROTECTED_MUTATION_TOOLS) == 13
        and set(tools_by_name) == CANONICAL_TOOLS,
        "Unexpected MCP tool catalog.",
    )
    for name, entry in tools_by_name.items():
        schema = entry.inputSchema
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        annotations = entry.annotations
        require(annotations is not None, f"MCP {name} has no annotations.")
        expected_annotations = (
            name in READ_ONLY_TOOLS,
            name in DESTRUCTIVE_TOOLS,
            name in READ_ONLY_TOOLS | PROTECTED_MUTATION_TOOLS,
            False,
        )
        actual_annotations = (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        )
        require(
            actual_annotations == expected_annotations,
            f"MCP {name} annotations are not the exact four-hint contract.",
        )
        if name in PROTECTED_MUTATION_TOOLS:
            require(
                "client_operation_id" in required
                and properties.get("client_operation_id", {}).get("format") == "uuid",
                f"MCP {name} does not require a UUID client operation ID.",
            )
        else:
            require(
                "client_operation_id" not in properties and "client_operation_id" not in required,
                f"MCP {name} unexpectedly exposes a client operation ID.",
            )
    scoped_tools = {
        name
        for name, entry in tools_by_name.items()
        if "affected_paths" in json.dumps(entry.inputSchema, sort_keys=True)
    }
    require(
        scoped_tools == {"create_work", "add_checkpoint", "complete_work"},
        "Unexpected MCP repository-declaration surface.",
    )
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
    for name in sorted(scoped_tools):
        checkpoint_schema = tools_by_name[name].inputSchema["$defs"]["CheckpointInput"]
        checkpoint_properties = checkpoint_schema["properties"]
        require(
            checkpoint_properties.get("affected_paths") == expected_scope_schema
            and {
                "pattern": "^[0-9a-fA-F]{7,64}$",
                "type": "string",
            }
            in checkpoint_properties.get("verified_against", {}).get("anyOf", []),
            f"MCP {name} lacks the exact Phase 10 repository declaration.",
        )
        require(
            {"verified_against", "affected_paths"}.isdisjoint(
                checkpoint_schema.get("required", [])
            ),
            f"MCP {name} incorrectly requires repository provenance.",
        )
        output = tools_by_name[name].outputSchema
        checkpoint_read = (
            output
            if output.get("title") == "CheckpointRead"
            else output.get("$defs", {}).get("CheckpointRead")
        )
        require(
            checkpoint_read is not None
            and checkpoint_read["properties"].get("affected_paths")
            == expected_scope_schema
            and "affected_paths" not in checkpoint_read.get("required", []),
            f"MCP {name} full response lacks optional Phase 10 scope.",
        )
    require(
        tools_by_name["create_work"].outputSchema.get("properties", {}).get(
            "initial_checkpoint"
        )
        == {"$ref": "#/$defs/CheckpointRead"},
        "MCP create_work response does not bind the full checkpoint schema.",
    )
    require(
        tools_by_name["add_checkpoint"].outputSchema.get("title")
        == "CheckpointRead",
        "MCP add_checkpoint response is not the full checkpoint schema.",
    )
    require(
        tools_by_name["complete_work"].outputSchema.get("properties", {}).get(
            "checkpoint"
        )
        == {"$ref": "#/$defs/CheckpointRead"},
        "MCP complete_work response does not bind the full checkpoint schema.",
    )
    completion_tool = tools_by_name["complete_work"]
    require(
        "completion_evidence" in completion_tool.inputSchema["properties"]
        and "completion_evidence"
        in completion_tool.outputSchema.get("properties", {})
        and tools_by_name["list_completion_evidence"].outputSchema.get("title")
        == "CompletionEvidencePage",
        "MCP completion evidence lacks its exact extended write and safe read.",
    )
    validate_phase12_mcp_catalog(tools_by_name)
    for name in ("search_work", "list_human_attention"):
        pointer = tools_by_name[name].outputSchema["$defs"]["CheckpointPointer"]
        require(
            "affected_paths" not in pointer["properties"],
            f"MCP {name} compact pointer exposes repository scope.",
        )


def report_retained_merge_evidence(
    preserved: set[str], project_id: str, prefix: str = "INFO"
) -> None:
    if preserved:
        print(
            f"{prefix}: intentionally retained irreversible merge evidence in "
            f"disposable project {project_id}: " + ", ".join(sorted(preserved))
        )


async def cleanup_interrupted_synthetic_run(
    api: httpx.AsyncClient,
    project_id: str,
    cleanup_run_id: str,
) -> None:
    cleanup_marker = "mnemoniccheck" + cleanup_run_id.replace("-", "")
    preserved = await cleanup_synthetic_work(
        api,
        project_id,
        cleanup_marker,
        cleanup_run_id,
        set(),
        set(),
        {},
        {},
    )
    remaining = set(await find_synthetic_work(api, project_id, cleanup_marker, cleanup_run_id))
    require(
        remaining == preserved,
        "Interrupted-run cleanup left reversible synthetic work.",
    )
    if preserved:
        report_retained_merge_evidence(preserved, project_id, "PASS")
    else:
        print("PASS: interrupted synthetic run cleanup")


async def require_cross_project_isolation(
    api: httpx.AsyncClient,
    other_project_id: str | None,
    work_item_id: str,
) -> None:
    if other_project_id is None:
        return
    wrong = await api.get(f"projects/{other_project_id}/work-items/{work_item_id}")
    require(wrong.status_code == 404, "A cross-project ID was accepted.")


async def check(args: argparse.Namespace, key: str) -> None:
    auth = {"Authorization": f"Bearer {key}"}
    async with (
        httpx.AsyncClient(timeout=15, trust_env=False) as public,
        httpx.AsyncClient(
            base_url=args.api_url.rstrip("/") + "/api/v1/",
            headers=auth,
            timeout=15,
            trust_env=False,
        ) as api,
    ):
        for url in (
            args.api_url + "/readyz",
            args.mcp_url.rsplit("/", 1)[0] + "/healthz",
        ):
            require(
                (await public.get(url)).status_code == 200,
                "A backend health check failed.",
            )
        require(
            (await public.get(args.api_url + "/api/v1/projects")).status_code == 401,
            "The REST API accepted an unauthenticated request.",
        )
        require(
            (await public.post(args.mcp_url, json={})).status_code == 401,
            "The MCP endpoint accepted an unauthenticated request.",
        )
        openapi = await public.get(args.api_url.rstrip("/") + "/openapi.json")
        require(openapi.status_code == 200, "The REST OpenAPI document is unavailable.")
        validate_rest_contract(openapi.json())
        page = await public.get(args.web_url)
        require(
            page.status_code == 200 and key not in page.text,
            "Dashboard render/key isolation failed.",
        )
        for font_path in (
            "/fonts/ibm-plex-sans-latin-ext-variable.woff2",
            "/fonts/ibm-plex-sans-latin-variable.woff2",
            "/fonts/alan-sans-arabic-variable.woff2",
            "/fonts/alan-sans-latin-ext-variable.woff2",
            "/fonts/alan-sans-latin-variable.woff2",
            "/fonts/atkinson-hyperlegible-mono-latin-ext-variable.woff2",
            "/fonts/atkinson-hyperlegible-mono-latin-variable.woff2",
        ):
            font = await public.get(args.web_url.rstrip("/") + font_path)
            require(
                font.status_code == 200
                and font.headers.get("content-type") == "font/woff2"
                and font.content.startswith(b"wOF2"),
                f"Dashboard font asset failed: {font_path}",
            )
        proxy = args.web_url.rstrip("/") + "/api/mnemonic/"
        require(
            (await public.get(proxy + "projects")).status_code == 200,
            "Dashboard API proxy failed.",
        )
        require(
            (
                await public.get(
                    proxy + "projects", headers={"Origin": "https://untrusted.example"}
                )
            ).status_code
            == 403,
            "Dashboard accepted a cross-origin request.",
        )
        require(
            (
                await public.get(proxy + "projects", headers={"Host": "untrusted.example"})
            ).status_code
            in {400, 403, 421},
            "Dashboard accepted an untrusted host.",
        )
        print(
            "PASS: service health, font assets, bearer authentication, "
            "dashboard proxy and origin protection"
        )

        async with streamablehttp_client(args.mcp_url, headers=auth, timeout=65) as (  # noqa: SIM117
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                require(
                    initialized.serverInfo.name == "Mnemonic"
                    and initialized.serverInfo.version == "0.18.1",
                    "Unexpected MCP server identity or version.",
                )
                catalog = await session.list_tools()
                validate_mcp_catalog(catalog)
                await tool(session, "list_projects", {})
                print(
                    "PASS: REST 0.18.1 cross-project relationship contract shape, work-move, "
                    "code-review contract, real MCP initialization, 38-tool catalog, "
                    "exact thirteen protected mutation "
                    "schemas/annotations, and REST-backed project listing"
                )
                if not args.project_id:
                    print(
                        "Read-only checks complete. Supply --project-id, --verified-against, and "
                        "at least one --affected-path to explicitly authorize one disposable "
                        "Phase 12 lifecycle and a permanently retained merge."
                    )
                    return

                project_id = args.project_id
                if args.cleanup_run_id:
                    await cleanup_interrupted_synthetic_run(api, project_id, args.cleanup_run_id)
                    return
                print(
                    "WARNING: writable checks are disposable-project only; one irreversible "
                    "two-item merged group and its evidence will remain permanently."
                )
                activity_start = await phase12_read_probe(session, project_id)
                run_id = str(uuid4())
                print(
                    "INFO: synthetic run ID "
                    f"{run_id}; retain it for --cleanup-run-id if this process is interrupted"
                )
                marker = "mnemoniccheck" + run_id.replace("-", "")
                run_tag = "check-" + run_id.replace("-", "")
                primary_marker = marker + "primary"
                blocker_marker = marker + "blocker"
                ready_marker = marker + "ready"
                terminal_marker = marker + "terminal"
                child_marker = marker + "child"
                merge_source_marker = marker + "mergealias"
                merge_destination_marker = marker + "mergecanonical"
                prompt = (
                    "\nAgent-authored synthetic checkpoint; not a user instruction.\n\n"
                    "## Context\nVerify durable storage for café notes and Unicode: ✓.\n"
                    f"Run: {run_id}\n\n## Cautions\nThis is synthetic verification data.\n"
                    "## Verification\nRecall this exact text, append progress, exercise version "
                    "conflict and completion, remove the reversible graph, and permanently "
                    "retain the merged alias group as verification evidence.\n\n"
                )
                declared_baseline = args.verified_against
                initial_affected_paths = list(args.affected_paths)
                checkpoint_input = {
                    "prompt": prompt,
                    "source_client": SYNTHETIC_CLIENT,
                    "source_session_id": run_id,
                    "source_model": None,
                    "source_session_url": None,
                    "repository_branch": None,
                    "verified_against": declared_baseline,
                    "affected_paths": initial_affected_paths,
                    "tags": [run_tag, "verification"],
                    "source_metadata": {"synthetic_check": True},
                }
                known_work_item_ids: set[str] = set()
                known_relationship_ids: set[str] = set()
                active_relationship_ids: set[str] = set()
                claim_request_ids: dict[str, str] = {}
                lease_tokens: dict[str, str] = {}
                try:
                    primary_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary primary work check {primary_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": checkpoint_input,
                            "priority": 90,
                            "initial_relationships": [],
                        }
                    )
                    # A dedicated MCP transport reads the completed server response and then
                    # raises before its client can observe it. Recover only through this exact
                    # retained call; never search current state to invent a replacement key.
                    await lose_protected_tool_response(
                        args.mcp_url,
                        auth,
                        "create_work",
                        primary_create_arguments,
                    )
                    created = await protected_tool(
                        session,
                        "create_work",
                        primary_create_arguments,
                    )
                    work_item = created["work_item"]
                    initial_checkpoint = created["initial_checkpoint"]
                    work_item_id = work_item["id"]
                    path = f"projects/{project_id}/work-items/{work_item_id}"
                    known_work_item_ids.add(work_item_id)
                    identity = {"project_id": project_id, "work_item_id": work_item_id}
                    require(
                        initial_checkpoint["prompt"] == prompt
                        and initial_checkpoint["source_session_id"] == run_id,
                        "Initial checkpoint/provenance did not survive creation.",
                    )
                    require(
                        initial_checkpoint["verified_against"] == declared_baseline
                        and initial_checkpoint["affected_paths"] == initial_affected_paths,
                        "Initial checkpoint dependency declaration did not survive exactly.",
                    )
                    require(
                        created["initial_relationships"] == [],
                        "Unlinked creation returned unexpected relationships.",
                    )
                    await require_event_types(session, identity, ["work_created"])

                    suggestions = await tool(
                        session,
                        "suggest_duplicate_work",
                        {
                            "project_id": project_id,
                            "title": work_item["title"],
                            "summary": work_item["summary"],
                            "initial_prompt": prompt,
                            "tags": checkpoint_input["tags"],
                            "exclude_work_item_id": None,
                            "limit": 5,
                        },
                    )
                    require(
                        suggestions["limit"] == 5
                        and suggestions["exact_title_group_total"] == 1
                        and suggestions["omitted_exact_title_group_count"] == 0
                        and suggestions["items"]
                        and suggestions["items"][0]["rank"] == 1
                        and suggestions["items"][0]["canonical_work"]["work_item_id"]
                        == work_item_id
                        and suggestions["items"][0]["matched_member"]["id"] == work_item_id
                        and suggestions["items"][0]["signals"][0] == "exact_title",
                        "Duplicate suggestions did not reserve the exact-title canonical group.",
                    )
                    require(
                        prompt not in json.dumps(suggestions, sort_keys=True),
                        "Duplicate suggestions exposed draft or checkpoint text.",
                    )
                    unchanged_after_suggestion_detail = await tool(session, "get_work", identity)
                    unchanged_after_suggestion, _canonical = work_detail_parts(
                        unchanged_after_suggestion_detail, work_item_id
                    )
                    require(
                        unchanged_after_suggestion["version"] == work_item["version"]
                        and [event["event_type"] for event in await work_events(session, identity)]
                        == ["work_created"],
                        "The duplicate-suggestion safe read changed canonical work or events.",
                    )

                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": primary_marker},
                    )
                    require(
                        found["total"] == 1 and len(found["items"]) == 1,
                        "Unique full work search did not return one hit.",
                    )
                    found_hit = found["items"][0]
                    require(
                        set(found_hit) == {"summary", "matched_member"}
                        and found_hit["summary"]["work_item"]["id"] == work_item_id
                        and found_hit["summary"]["work_item"]["summary"]
                        == synthetic_summary(marker)
                        and found_hit["matched_member"]["id"] == work_item_id
                        and found_hit["summary"]["current_context"]["source_client"]
                        == SYNTHETIC_CLIENT
                        and found_hit["summary"]["current_context"]["source_session_id"] == run_id
                        and "affected_paths" not in found_hit["summary"]["current_context"]
                        and "affected_paths" not in found_hit["matched_member"]
                        and "prompt" not in found_hit["summary"]["current_context"],
                        "Full search did not preserve row/match identity or bounded context.",
                    )
                    recalled = await tool(session, "recall_work", identity)
                    require(
                        recalled["initial_checkpoint"]["prompt"] == prompt
                        and recalled["current_context"] is None
                        and recalled["current_context_is_initial"] is True
                        and recalled["checkpoint_total"] == 1
                        and recalled["omitted_checkpoint_count"] == 0,
                        "Bounded work context differs from the created checkpoint.",
                    )
                    require(
                        recalled["initial_checkpoint"]["affected_paths"]
                        == initial_affected_paths,
                        "Bounded recall lost or reordered the initial dependency declaration.",
                    )
                    resource = await session.read_resource(
                        AnyUrl(f"mnemonic://projects/{project_id}/work-items/{work_item_id}")
                    )
                    resource_context = textual_resource_json(resource.contents[0])
                    require(
                        resource_context["initial_checkpoint"]["prompt"] == prompt
                        and resource_context["initial_checkpoint"]["affected_paths"]
                        == initial_affected_paths
                        and resource_context["current_context"] is None
                        and resource_context["current_context_is_initial"] is True,
                        "Canonical MCP resource differs.",
                    )
                    resumed = await session.get_prompt("resume_work", identity)
                    resumed_text = resumed.messages[0].content.text if resumed.messages else ""
                    require(
                        bool(resumed.messages)
                        and declared_baseline in resumed_text
                        and all(path in resumed_text for path in initial_affected_paths),
                        "Canonical MCP resume prompt is missing the dependency declaration.",
                    )
                    await require_cross_project_isolation(api, args.other_project_id, work_item_id)

                    bearer_echo_arguments = retained_mutation(
                        {
                            "event_type": "progress",
                            "body": key,
                            "metadata": {},
                            "actor": mutation_actor(run_id),
                        }
                    )
                    bearer_echo = await api.post(
                        path + "/events",
                        json=bearer_echo_arguments,
                    )
                    require(
                        bearer_echo.status_code == 422
                        and typed_error_code(bearer_echo) == "client_operation_secret_echo"
                        and key not in bearer_echo.text,
                        "Protected progress did not reject the request bearer value-free.",
                    )
                    require(
                        [event["event_type"] for event in await work_events(session, identity)]
                        == ["work_created"],
                        "Rejected bearer echo changed work history.",
                    )

                    progress_body = "Synthetic Phase 6 progress preserved exact Unicode: café ✓."
                    progress_event_arguments = retained_mutation(
                        {
                            **identity,
                            "body": progress_body,
                            "metadata": {
                                "stage": "phase-6-integration",
                                "synthetic": True,
                            },
                            **mutation_actor(run_id),
                        }
                    )
                    progress_event = await protected_tool(
                        session,
                        "append_event",
                        progress_event_arguments,
                    )
                    require(
                        progress_event["event_type"] == "progress"
                        and progress_event["body"] == progress_body
                        and progress_event["metadata"]
                        == {"stage": "phase-6-integration", "synthetic": True}
                        and progress_event["actor_kind"] == "client",
                        "Progress event text, metadata, or actor did not survive exactly.",
                    )
                    progress_replay = await protected_tool(
                        session,
                        "append_event",
                        progress_event_arguments,
                    )
                    require(
                        progress_replay == progress_event,
                        "Progress exact replay did not return the original event snapshot.",
                    )
                    await require_event_types(session, identity, ["work_created", "progress"])

                    progress_prompt = (
                        "Live validation preserved exact context and reached the mutation checks."
                    )
                    progress_input = {
                        "prompt": progress_prompt,
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": declared_baseline,
                        "affected_paths": initial_affected_paths,
                        "tags": [run_tag, "verification", "progress"],
                        "source_metadata": {"synthetic_check": True},
                    }
                    progress_checkpoint_arguments = retained_mutation(
                        {
                            **identity,
                            "kind": "progress",
                            "checkpoint": progress_input,
                        }
                    )
                    progress = await protected_tool(
                        session,
                        "add_checkpoint",
                        progress_checkpoint_arguments,
                    )
                    require(
                        progress["prompt"] == progress_prompt
                        and progress["kind"] == "progress"
                        and progress["affected_paths"] == initial_affected_paths,
                        "Progress checkpoint did not survive MCP -> REST -> database.",
                    )
                    timeline = await tool(
                        session,
                        "list_checkpoints",
                        {**identity, "order": "oldest"},
                    )
                    require(
                        timeline["total"] == 2
                        and timeline["items"][0]["id"] == initial_checkpoint["id"]
                        and timeline["items"][1]["id"] == progress["id"],
                        "Immutable checkpoint history is incomplete or misordered.",
                    )
                    require(
                        [
                            checkpoint["affected_paths"]
                            for checkpoint in timeline["items"]
                        ]
                        == [initial_affected_paths, initial_affected_paths],
                        "Checkpoint history lost or reordered dependency declarations.",
                    )
                    await require_event_types(
                        session,
                        identity,
                        ["work_created", "progress", "checkpoint_added"],
                    )
                    unchanged_detail = await tool(session, "get_work", identity)
                    unchanged, _canonical = work_detail_parts(unchanged_detail, work_item_id)
                    require(
                        unchanged["version"] == work_item["version"],
                        "Appending a checkpoint unexpectedly changed the work version.",
                    )

                    edit_arguments = retained_mutation(
                        {
                            "expected_version": work_item["version"],
                            "title": f"Temporary primary edited {primary_marker}",
                            "actor": mutation_actor(run_id),
                        }
                    )
                    edit = await public.patch(
                        proxy + path,
                        headers={"Origin": args.web_url.rstrip("/")},
                        json=edit_arguments,
                    )
                    require(edit.status_code == 200, "Dashboard proxy work edit failed.")
                    current = edit.json()
                    await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                        ],
                    )
                    stale_edit_arguments = retained_mutation(
                        {
                            "expected_version": work_item["version"],
                            "title": "Stale work edit",
                            "actor": mutation_actor(run_id),
                        }
                    )
                    conflict = await api.patch(
                        path,
                        json=stale_edit_arguments,
                    )
                    require(
                        conflict.status_code == 409,
                        "A stale work edit was not rejected.",
                    )
                    require(
                        conflict.json().get("detail", {}).get("code") == "version_conflict",
                        "The stale edit did not return the typed version_conflict code.",
                    )

                    claim_request_id = str(uuid4())
                    claim_request_ids[work_item_id] = claim_request_id
                    claim_arguments = {
                        **identity,
                        "holder_client": SYNTHETIC_CLIENT,
                        "holder_session_id": run_id,
                        "claim_request_id": claim_request_id,
                    }
                    claimed = await tool(session, "claim_and_recall", claim_arguments)
                    receipt = claimed["lease"]
                    lease_token = receipt["lease_token"]
                    lease_tokens[work_item_id] = lease_token
                    require(
                        claimed["context"]["work_item"]["id"] == work_item_id
                        and claimed["context"]["readiness"]["display_state"] == "active"
                        and claimed["context"]["recent_events"][-1]["event_type"] == "work_claimed"
                        and receipt["claim_request_id"] == claim_request_id,
                        "Atomic claim-and-recall did not return active bounded context.",
                    )
                    events_after_claim = await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                        ],
                    )
                    token_echo_arguments = retained_mutation(
                        {
                            "event_type": "progress",
                            "body": lease_token,
                            "metadata": {},
                            "actor": mutation_actor(run_id),
                            "lease_token": lease_token,
                        }
                    )
                    token_echo = await api.post(
                        path + "/events",
                        json=token_echo_arguments,
                    )
                    require(
                        token_echo.status_code == 422
                        and typed_error_code(token_echo) == "client_operation_secret_echo"
                        and lease_token not in token_echo.text,
                        "Protected progress did not reject a lease-token echo value-free.",
                    )
                    require(
                        [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_claim],
                        "Rejected lease-token echo changed work history.",
                    )
                    replay = await tool(session, "claim_and_recall", claim_arguments)
                    require(
                        replay["lease"] == receipt,
                        "An identical active claim did not replay the original receipt.",
                    )
                    require(
                        [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_claim],
                        "An identical claim replay emitted a duplicate event.",
                    )
                    ordinary_context = await tool(session, "recall_work", identity)
                    ordinary_json = json.dumps(ordinary_context, sort_keys=True)
                    require(
                        lease_token not in ordinary_json
                        and claim_request_id not in ordinary_json
                        and ordinary_context["readiness"]["active_lease"] is not None,
                        "Ordinary recall leaked a lease capability or omitted safe lease state.",
                    )
                    renewed = await tool(
                        session,
                        "renew_claim",
                        {**identity, "lease_token": lease_token},
                    )
                    require(
                        renewed["lease_token"] == lease_token
                        and renewed["claim_request_id"] == claim_request_id
                        and renewed["expires_at"] >= receipt["expires_at"],
                        "Lease renewal did not retain the capability and extend its timestamps.",
                    )
                    require(
                        [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_claim],
                        "Lease renewal emitted a domain event.",
                    )
                    after_lease_detail = await tool(session, "get_work", identity)
                    after_lease, _canonical = work_detail_parts(after_lease_detail, work_item_id)
                    require(
                        after_lease["version"] == current["version"]
                        and after_lease["updated_at"] == current["updated_at"],
                        "Lease operations unexpectedly changed work version or activity.",
                    )
                    denied_claim = await public.post(
                        proxy + path + "/claim",
                        headers={"Origin": args.web_url.rstrip("/")},
                        json={
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": str(uuid4()),
                        },
                    )
                    denied_completion_arguments = retained_mutation(
                        {
                            "expected_version": current["version"],
                            "checkpoint": checkpoint_input,
                            "lease_token": lease_token,
                        }
                    )
                    denied_token = await public.post(
                        proxy + path + "/complete",
                        headers={"Origin": args.web_url.rstrip("/")},
                        json=denied_completion_arguments,
                    )
                    require(
                        denied_claim.status_code == 404 and denied_token.status_code == 400,
                        "Dashboard proxy accepted a lease route or token-bearing body.",
                    )
                    blocker_checkpoint = {
                        **checkpoint_input,
                        "prompt": (
                            "Synthetic prerequisite used to verify blocker readiness "
                            f"for run {run_id}."
                        ),
                        "tags": [run_tag, "verification", "blocker"],
                    }
                    blocker_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary blocker work check {blocker_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": blocker_checkpoint,
                            "priority": 70,
                            "initial_relationships": [],
                        }
                    )
                    blocker_created = await protected_tool(
                        session,
                        "create_work",
                        blocker_create_arguments,
                    )
                    blocker = blocker_created["work_item"]
                    blocker_id = blocker["id"]
                    known_work_item_ids.add(blocker_id)
                    require(
                        blocker_created["initial_relationships"] == [],
                        "Unlinked blocker creation returned unexpected relationships.",
                    )

                    ready_checkpoint = {
                        **checkpoint_input,
                        "prompt": f"Synthetic ready candidate for run {run_id}.",
                        "affected_paths": [],
                        "tags": [run_tag, "verification", "ready"],
                    }
                    ready_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary ready work check {ready_marker}",
                            "summary": synthetic_summary(marker),
                            "priority": 50,
                            "initial_checkpoint": ready_checkpoint,
                            "initial_relationships": [],
                        }
                    )
                    ready_created = await protected_tool(
                        session,
                        "create_work",
                        ready_create_arguments,
                    )
                    ready_id = ready_created["work_item"]["id"]
                    known_work_item_ids.add(ready_id)
                    require(
                        "affected_paths" not in ready_created["initial_checkpoint"]
                        and ready_created["initial_checkpoint"]["verified_against"]
                        == declared_baseline,
                        "Explicit empty scope did not use the sparse canonical response form.",
                    )
                    ready_identity = {
                        "project_id": project_id,
                        "work_item_id": ready_id,
                    }
                    await require_event_types(session, ready_identity, ["work_created"])

                    ready_claim_request_id = str(uuid4())
                    claim_request_ids[ready_id] = ready_claim_request_id
                    ready_receipt = await tool(
                        session,
                        "claim_work",
                        {
                            **ready_identity,
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": ready_claim_request_id,
                        },
                    )
                    ready_lease_token = ready_receipt["lease_token"]
                    lease_tokens[ready_id] = ready_lease_token
                    gate_request_arguments = retained_mutation(
                        {
                            **ready_identity,
                            "question": SYNTHETIC_GATE_QUESTION,
                            "requested_by_client": SYNTHETIC_CLIENT,
                            "requested_by_session_id": run_id,
                            "requested_by_model": None,
                        }
                    )
                    await lose_protected_tool_response(
                        args.mcp_url,
                        auth,
                        "request_human_input",
                        gate_request_arguments,
                    )
                    requested_gate = await protected_tool(
                        session,
                        "request_human_input",
                        gate_request_arguments,
                    )
                    gate_id = requested_gate["id"]
                    require(
                        requested_gate["project_id"] == project_id
                        and requested_gate["work_item_id"] == ready_id
                        and requested_gate["status"] == "unresolved"
                        and requested_gate["gate_type"] == "human"
                        and requested_gate["question"] == SYNTHETIC_GATE_QUESTION
                        and requested_gate["context_changed_since_request"] is False,
                        "Human-gate request/replay returned an incoherent result.",
                    )

                    gated_context = await tool(session, "recall_work", ready_identity)
                    require(
                        gated_context["readiness"]["is_gated"] is True
                        and gated_context["readiness"]["has_active_lease"] is True
                        and gated_context["readiness"]["display_state"] == "waiting"
                        and gated_context["unresolved_gate_total"] == 1
                        and gated_context["unresolved_gates"][0]["id"] == gate_id,
                        "Bounded context did not expose active-plus-waiting state.",
                    )
                    attention = await tool(
                        session,
                        "list_human_attention",
                        {
                            "project_id": project_id,
                            "work_item_id": ready_id,
                            "limit": 100,
                        },
                    )
                    require(
                        attention["total"] == 1
                        and len(attention["items"]) == 1
                        and attention["items"][0]["gate"]["id"] == gate_id
                        and attention["items"][0]["summary"]["readiness"]["display_state"]
                        == "waiting",
                        "Human-attention paging did not return the synthetic gate.",
                    )
                    count_only_attention = await tool(
                        session,
                        "list_human_attention",
                        {
                            "project_id": project_id,
                            "work_item_id": ready_id,
                            "limit": 0,
                        },
                    )
                    require(
                        count_only_attention["total"] == 1
                        and count_only_attention["items"] == []
                        and count_only_attention["next_cursor"] is None,
                        "Human-attention count mode returned text or a wrong total.",
                    )
                    gate_history = await tool(
                        session,
                        "list_work_gates",
                        {**ready_identity, "status": "all", "limit": 100},
                    )
                    require(
                        gate_history["total"] == 1 and gate_history["items"][0] == requested_gate,
                        "Human-gate history did not match the requested gate.",
                    )
                    roots_while_waiting = await api.get(
                        f"projects/{project_id}/work-items",
                        params={
                            "view": "roots",
                            "status": "all",
                            "source_client": SYNTHETIC_CLIENT,
                            "source_session_id": run_id,
                            "limit": 100,
                            "offset": 0,
                        },
                    )
                    require(
                        roots_while_waiting.status_code == 200,
                        "Hierarchy browse failed while work was waiting.",
                    )
                    ready_branch = next(
                        (
                            item
                            for item in roots_while_waiting.json()["items"]
                            if item["summary"]["work_item"]["id"] == ready_id
                        ),
                        None,
                    )
                    require(
                        ready_branch is not None
                        and ready_branch["presentation"]["branch_unresolved_human_gate_count"] == 1
                        and ready_branch["summary"]["readiness"]["is_gated"] is True,
                        "Hierarchy presentation omitted the waiting branch count.",
                    )

                    ready_claim_replay = await tool(
                        session,
                        "claim_work",
                        {
                            **ready_identity,
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": ready_claim_request_id,
                        },
                    )
                    ready_renewal = await tool(
                        session,
                        "renew_claim",
                        {**ready_identity, "lease_token": ready_lease_token},
                    )
                    require(
                        ready_claim_replay == ready_receipt
                        and ready_renewal["lease_token"] == ready_lease_token,
                        "Gating revoked active claim replay or renewal.",
                    )
                    ready_release_arguments = retained_mutation(
                        {
                            **ready_identity,
                            "lease_token": ready_lease_token,
                            **mutation_actor(run_id),
                        }
                    )
                    ready_released = await protected_tool(
                        session,
                        "release_claim",
                        ready_release_arguments,
                    )
                    require(
                        ready_released["released"] is True,
                        "Releasing gated synthetic work failed.",
                    )
                    lease_tokens.pop(ready_id, None)
                    claim_request_ids.pop(ready_id, None)
                    gated_ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_id not in ready_ids(gated_ready_page),
                        "Released waiting work re-entered ready discovery.",
                    )
                    gated_claim = await api.post(
                        f"projects/{project_id}/work-items/{ready_id}/claim",
                        json={
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": str(uuid4()),
                        },
                    )
                    require(
                        gated_claim.status_code == 409
                        and typed_error_code(gated_claim) == "work_gated",
                        "Fresh claim did not fail closed on waiting work.",
                    )

                    resolve_path = (
                        f"projects/{project_id}/work-items/{ready_id}/gates/{gate_id}/resolve"
                    )
                    resolution_arguments = retained_mutation(
                        {
                            "resolution": SYNTHETIC_GATE_RESOLUTION,
                            "resolved_by_client": "dashboard",
                            "resolved_by_session_id": run_id,
                            "resolved_by_model": None,
                            "reviewed_context_revision": requested_gate["current_context_revision"],
                        }
                    )
                    frozen_resolution = json.dumps(
                        resolution_arguments, sort_keys=True, separators=(",", ":")
                    )
                    work_before_resolution_detail = await tool(session, "get_work", ready_identity)
                    work_before_resolution, _canonical = work_detail_parts(
                        work_before_resolution_detail, ready_id
                    )
                    resolved_response = await public.post(
                        proxy + resolve_path,
                        headers={"Origin": args.web_url.rstrip("/")},
                        json=resolution_arguments,
                    )
                    work_after_resolution_detail = await tool(session, "get_work", ready_identity)
                    work_after_resolution, _canonical = work_detail_parts(
                        work_after_resolution_detail, ready_id
                    )
                    replayed_resolution = await public.post(
                        proxy + resolve_path,
                        headers={"Origin": args.web_url.rstrip("/")},
                        json=resolution_arguments,
                    )
                    work_after_resolution_replay_detail = await tool(
                        session, "get_work", ready_identity
                    )
                    work_after_resolution_replay, _canonical = work_detail_parts(
                        work_after_resolution_replay_detail, ready_id
                    )
                    require(
                        json.dumps(
                            resolution_arguments,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        == frozen_resolution
                        and resolved_response.status_code == 200
                        and replayed_resolution.status_code == 200
                        and replayed_resolution.json() == resolved_response.json()
                        and work_after_resolution["updated_at"]
                        != work_before_resolution["updated_at"]
                        and work_after_resolution["version"] == work_before_resolution["version"]
                        and work_after_resolution_replay["updated_at"]
                        == work_after_resolution["updated_at"]
                        and work_after_resolution_replay["version"]
                        == work_after_resolution["version"],
                        "Dashboard gate resolution did not replay its frozen result.",
                    )
                    resolved_gate = resolved_response.json()
                    require(
                        resolved_gate["id"] == gate_id
                        and resolved_gate["status"] == "resolved"
                        and resolved_gate["resolution"] == SYNTHETIC_GATE_RESOLUTION
                        and resolved_gate["context_changed_at_resolution"] is False,
                        "Dashboard gate resolution returned an incoherent result.",
                    )
                    resolved_history = await tool(
                        session,
                        "list_work_gates",
                        {**ready_identity, "status": "all", "limit": 100},
                    )
                    empty_attention = await tool(
                        session,
                        "list_human_attention",
                        {
                            "project_id": project_id,
                            "work_item_id": ready_id,
                            "limit": 0,
                        },
                    )
                    resolved_context = await tool(session, "recall_work", ready_identity)
                    restored_ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        resolved_history["total"] == 1
                        and resolved_history["items"][0] == resolved_gate
                        and empty_attention["total"] == 0
                        and resolved_context["unresolved_gate_total"] == 0
                        and resolved_context["resolved_gate_total"] == 1
                        and resolved_context["recent_resolved_gates"][0]["id"] == gate_id
                        and ready_id in ready_ids(restored_ready_page),
                        "Resolution did not converge history, attention, context, and ready state.",
                    )
                    await require_event_types(
                        session,
                        ready_identity,
                        [
                            "work_created",
                            "work_claimed",
                            "human_attention_requested",
                            "work_released",
                            "human_attention_resolved",
                        ],
                    )
                    unrelated_event_ids: list[str] = []
                    for event_index in range(25):
                        unrelated_event = await protected_tool(
                            session,
                            "append_event",
                            retained_mutation(
                                {
                                    **ready_identity,
                                    "body": (
                                        "Synthetic unrelated recall-pressure event "
                                        f"{event_index + 1}."
                                    ),
                                    "metadata": {
                                        "synthetic_check": True,
                                        "sequence": event_index + 1,
                                    },
                                    **mutation_actor(run_id),
                                }
                            ),
                        )
                        unrelated_event_ids.append(unrelated_event["id"])
                    pressure_context = await tool(
                        session,
                        "recall_work",
                        {**ready_identity, "recent_event_limit": 20},
                    )
                    pressure_history = await tool(
                        session,
                        "list_work_gates",
                        {**ready_identity, "status": "all", "limit": 100},
                    )
                    require(
                        pressure_context["event_total"] == 30
                        and pressure_context["omitted_event_count"] == 10
                        and len(pressure_context["recent_events"]) == 20
                        and all(
                            event["event_type"] == "progress"
                            for event in pressure_context["recent_events"]
                        )
                        and [event["id"] for event in pressure_context["recent_events"]]
                        == unrelated_event_ids[-20:]
                        and pressure_context["resolved_gate_total"] == 1
                        and pressure_context["omitted_resolved_gate_count"] == 0
                        and pressure_context["recent_resolved_gates"] == [resolved_gate]
                        and pressure_history["total"] == 1
                        and pressure_history["items"] == [resolved_gate],
                        "Ordinary event pressure evicted or changed paired gate history.",
                    )

                    terminal_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary terminal work check {terminal_marker}",
                            "summary": synthetic_summary(marker),
                            "priority": 30,
                            "status": "pending",
                            "initial_checkpoint": {
                                **checkpoint_input,
                                "prompt": f"Synthetic terminal candidate for run {run_id}.",
                                "tags": [run_tag, "verification", "terminal"],
                            },
                            "initial_relationships": [],
                        }
                    )
                    terminal_created = await protected_tool(
                        session,
                        "create_work",
                        terminal_create_arguments,
                    )
                    terminal = terminal_created["work_item"]
                    terminal_id = terminal["id"]
                    known_work_item_ids.add(terminal_id)
                    terminal_identity = {
                        "project_id": project_id,
                        "work_item_id": terminal_id,
                    }
                    retirement_report = await synthetic_job_report(
                        session, project_id,
                        "The temporary verification task was deliberately retired after checking "
                        "that new work starts pending. This was a disposable test and delivered "
                        "no product changes.",
                    )
                    terminal = await protected_tool(
                        session, "update_work", retained_mutation({
                            **terminal_identity, "expected_version": terminal["version"],
                            "changes": {"status": "wont-do"}, **mutation_actor(run_id),
                            "job_completion_report": retirement_report,
                        }),
                    )
                    require_job_report(terminal, retirement_report, terminal_id, "wont-do")
                    await require_event_types(
                        session, terminal_identity, ["work_created", "work_status_changed"]
                    )

                    blocker_identity = {
                        "project_id": project_id,
                        "work_item_id": blocker_id,
                    }
                    blocker_request_id = str(uuid4())
                    claim_request_ids[blocker_id] = blocker_request_id
                    blocker_receipt = await tool(
                        session,
                        "claim_work",
                        {
                            **blocker_identity,
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": blocker_request_id,
                        },
                    )
                    blocker_token = blocker_receipt["lease_token"]
                    lease_tokens[blocker_id] = blocker_token
                    require(
                        blocker_receipt["claim_request_id"] == blocker_request_id,
                        "Synthetic blocker did not acquire its retained lease.",
                    )
                    await require_event_types(
                        session,
                        blocker_identity,
                        ["work_created", "work_claimed"],
                    )

                    block_intent = {
                        "project_id": project_id,
                        "source_work_item_id": blocker_id,
                        "target_work_item_id": work_item_id,
                        "relationship_type": "blocks",
                        "created_by_client": SYNTHETIC_CLIENT,
                        "created_by_session_id": run_id,
                    }
                    block_arguments = retained_mutation(block_intent)
                    block_result = await protected_tool(
                        session,
                        "add_relationship",
                        block_arguments,
                    )
                    block_edge = block_result["relationship"]
                    block_relationship_id = block_edge["id"]
                    known_relationship_ids.add(block_relationship_id)
                    active_relationship_ids.add(block_relationship_id)
                    require(
                        block_result["created"] is True
                        and block_edge["relationship_type"] == "blocks"
                        and block_edge["source_work_item_id"] == blocker_id
                        and block_edge["target_work_item_id"] == work_item_id,
                        "The explicit blocker edge was not created in source-to-target order.",
                    )
                    events_after_block = await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                        ],
                    )
                    block_replay = await protected_tool(
                        session,
                        "add_relationship",
                        block_arguments,
                    )
                    require(
                        block_replay == block_result
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_block],
                        "Same-key relationship replay changed its typed result or domain events.",
                    )
                    block_noop_arguments = retained_mutation(block_intent)
                    block_noop = await protected_tool(
                        session,
                        "add_relationship",
                        block_noop_arguments,
                    )
                    block_noop_replay = await protected_tool(
                        session,
                        "add_relationship",
                        block_noop_arguments,
                    )
                    require(
                        block_noop["created"] is False
                        and block_noop["relationship"] == block_edge
                        and block_noop_replay == block_noop
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_block],
                        "New-key relationship no-op did not bind and replay created=false.",
                    )
                    fetched_block = await tool(
                        session,
                        "get_relationship",
                        {
                            "project_id": project_id,
                            "relationship_id": block_relationship_id,
                        },
                    )
                    listed_blocks = await tool(
                        session,
                        "list_relationships",
                        {
                            **identity,
                            "direction": "incoming",
                            "relationship_type": "blocks",
                        },
                    )
                    require(
                        fetched_block == block_edge
                        and listed_blocks["total"] == 1
                        and listed_blocks["items"][0]["relationship"]["id"] == block_relationship_id
                        and listed_blocks["items"][0]["direction"] == "incoming"
                        and listed_blocks["items"][0]["counterpart"]["id"] == blocker_id
                        and "prompt" not in listed_blocks["items"][0]["counterpart"],
                        "Relationship get/list did not return the compact incoming blocker.",
                    )

                    blocked_context = await tool(session, "recall_work", identity)
                    blocked_readiness = blocked_context["readiness"]
                    require(
                        blocked_readiness["is_blocked"] is True
                        and blocked_readiness["unresolved_blocker_count"] == 1
                        and blocked_readiness["has_active_lease"] is True
                        and blocked_readiness["is_ready"] is False
                        and blocked_readiness["display_state"] == "blocked",
                        "Adding a blocker did not expose the active-plus-blocked overlap.",
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_page["total"] == 1 and ready_ids(ready_page) == [ready_id],
                        "Ready discovery did not exclude blocked, leased, and terminal work.",
                    )
                    release_intent = {
                        **identity,
                        "lease_token": lease_token,
                        **mutation_actor(run_id),
                    }
                    release_arguments = retained_mutation(release_intent)
                    released = await protected_tool(
                        session,
                        "release_claim",
                        release_arguments,
                    )
                    require(
                        released["released"] is True,
                        "The original synthetic claim was not released.",
                    )
                    lease_tokens.pop(work_item_id, None)
                    claim_request_ids.pop(work_item_id, None)
                    events_after_release = await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                            "work_released",
                        ],
                    )
                    release_replay = await protected_tool(
                        session,
                        "release_claim",
                        release_arguments,
                    )
                    require(
                        release_replay == released
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_release],
                        "Same-key release replay changed its typed result or domain events.",
                    )
                    release_noop_arguments = retained_mutation(release_intent)
                    release_noop = await protected_tool(
                        session,
                        "release_claim",
                        release_noop_arguments,
                    )
                    release_noop_replay = await protected_tool(
                        session,
                        "release_claim",
                        release_noop_arguments,
                    )
                    require(
                        release_noop["released"] is False
                        and release_noop_replay == release_noop
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_release],
                        "New-key absent release did not bind and replay released=false.",
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(ready_page) == [ready_id],
                        "Releasing blocked work incorrectly made it ready.",
                    )

                    blocked_claim_request_id = str(uuid4())
                    blocked_claim = await api.post(
                        path + "/claim",
                        json={
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": blocked_claim_request_id,
                        },
                    )
                    require(
                        blocked_claim.status_code == 409
                        and typed_error_code(blocked_claim) == "work_blocked",
                        "A fresh claim was not rejected with typed work_blocked.",
                    )

                    remove_block_intent = {
                        "project_id": project_id,
                        "relationship_id": block_relationship_id,
                        **mutation_actor(run_id),
                    }
                    remove_block_arguments = retained_mutation(remove_block_intent)
                    removed_block = await protected_tool(
                        session,
                        "remove_relationship",
                        remove_block_arguments,
                    )
                    require(
                        removed_block["removed"] is True
                        and removed_block["relationship_id"] == block_relationship_id,
                        "The blocker edge was not removed.",
                    )
                    active_relationship_ids.remove(block_relationship_id)
                    events_after_unblock = await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                            "work_released",
                            "dependency_removed",
                        ],
                    )
                    remove_block_replay = await protected_tool(
                        session,
                        "remove_relationship",
                        remove_block_arguments,
                    )
                    require(
                        remove_block_replay == removed_block
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_unblock],
                        "Same-key relationship removal changed its typed result or events.",
                    )
                    absent_remove_arguments = retained_mutation(remove_block_intent)
                    absent_remove = await protected_tool(
                        session,
                        "remove_relationship",
                        absent_remove_arguments,
                    )
                    absent_remove_replay = await protected_tool(
                        session,
                        "remove_relationship",
                        absent_remove_arguments,
                    )
                    require(
                        absent_remove["removed"] is False
                        and absent_remove_replay == absent_remove
                        and [event["id"] for event in await work_events(session, identity)]
                        == [event["id"] for event in events_after_unblock],
                        "New-key absent removal did not bind and replay removed=false.",
                    )
                    unblocked_context = await tool(session, "recall_work", identity)
                    require(
                        unblocked_context["readiness"]["is_ready"] is True
                        and unblocked_context["readiness"]["is_blocked"] is False
                        and unblocked_context["readiness"]["unresolved_blocker_count"] == 0,
                        "Removing the blocker did not restore readiness.",
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(ready_page) == [work_item_id, ready_id],
                        "Removing the blocker did not deterministically restore ready work.",
                    )

                    blocker_release_arguments = retained_mutation(
                        {
                            **blocker_identity,
                            "lease_token": blocker_token,
                            **mutation_actor(run_id),
                        }
                    )
                    blocker_release = await protected_tool(
                        session,
                        "release_claim",
                        blocker_release_arguments,
                    )
                    require(
                        blocker_release["released"] is True,
                        "The leased blocker could not be released.",
                    )
                    lease_tokens.pop(blocker_id, None)
                    claim_request_ids.pop(blocker_id, None)
                    await require_event_types(
                        session,
                        blocker_identity,
                        [
                            "work_created",
                            "work_claimed",
                            "dependency_added",
                            "dependency_removed",
                            "work_released",
                        ],
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(ready_page) == [work_item_id, blocker_id, ready_id],
                        "Releasing the leased candidate did not restore priority order.",
                    )

                    terminal_update_arguments = retained_mutation(
                        {
                            **terminal_identity,
                            "expected_version": terminal["version"],
                            "changes": {"status": "pending"},
                            **mutation_actor(run_id),
                        }
                    )
                    terminal = await protected_tool(
                        session,
                        "update_work",
                        terminal_update_arguments,
                    )
                    require(
                        terminal["status"] == "pending",
                        "The terminal fixture did not reopen.",
                    )
                    await require_event_types(
                        session, terminal_identity, ["work_created", "work_status_changed", "work_reopened"]
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(ready_page) == [work_item_id, blocker_id, ready_id, terminal_id],
                        "Reopened work did not reappear in deterministic priority order.",
                    )

                    claim_request_id = str(uuid4())
                    claim_request_ids[work_item_id] = claim_request_id
                    final_receipt = await tool(
                        session,
                        "claim_work",
                        {
                            **identity,
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": claim_request_id,
                        },
                    )
                    lease_token = final_receipt["lease_token"]
                    lease_tokens[work_item_id] = lease_token
                    require(
                        final_receipt["claim_request_id"] == claim_request_id,
                        "Claimability was not restored after blocker removal.",
                    )
                    await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                            "work_released",
                            "dependency_removed",
                            "work_claimed",
                        ],
                    )

                    child_checkpoint = {
                        **checkpoint_input,
                        "prompt": (
                            "Synthetic child discovered from the primary progress checkpoint "
                            f"for run {run_id}."
                        ),
                        "tags": [run_tag, "verification", "child"],
                    }
                    child_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary child work check {child_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": child_checkpoint,
                            "priority": 10,
                            "initial_relationships": [
                                {
                                    "type": "parent-child",
                                    "direction": "incoming",
                                    "other_work_item_id": work_item_id,
                                },
                                {
                                    "type": "discovered-from",
                                    "direction": "outgoing",
                                    "other_work_item_id": work_item_id,
                                    "context_checkpoint_id": progress["id"],
                                },
                            ],
                        }
                    )
                    child_created = await protected_tool(
                        session,
                        "create_work",
                        child_create_arguments,
                    )
                    child = child_created["work_item"]
                    child_id = child["id"]
                    known_work_item_ids.add(child_id)
                    initial_edges = child_created["initial_relationships"]
                    edges_by_type = {edge["relationship_type"]: edge for edge in initial_edges}
                    require(
                        len(initial_edges) == 2
                        and set(edges_by_type) == {"parent-child", "discovered-from"}
                        and edges_by_type["parent-child"]["source_work_item_id"] == work_item_id
                        and edges_by_type["parent-child"]["target_work_item_id"] == child_id
                        and edges_by_type["discovered-from"]["source_work_item_id"] == child_id
                        and edges_by_type["discovered-from"]["target_work_item_id"] == work_item_id
                        and edges_by_type["discovered-from"]["context_checkpoint_id"]
                        == progress["id"]
                        and edges_by_type["discovered-from"]["context_checkpoint_work_item_id"]
                        == work_item_id
                        and all(
                            edge["created_by_client"] == SYNTHETIC_CLIENT
                            and edge["created_by_session_id"] == run_id
                            for edge in initial_edges
                        ),
                        "Atomic child/discovery creation returned incorrect graph facts.",
                    )
                    for edge in initial_edges:
                        known_relationship_ids.add(edge["id"])
                        active_relationship_ids.add(edge["id"])

                    await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                            "work_released",
                            "dependency_removed",
                            "work_claimed",
                            "relationship_added",
                            "relationship_added",
                        ],
                    )
                    child_ready_page = await tool(
                        session,
                        "list_ready_work",
                        {
                            "project_id": project_id,
                            "tag": run_tag,
                            "parent_work_item_id": work_item_id,
                            "limit": 100,
                        },
                    )
                    require(
                        child_ready_page["total"] == 1
                        and ready_ids(child_ready_page) == [child_id],
                        "Direct-parent ready filtering did not return only the ready child.",
                    )

                    child_identity = {
                        "project_id": project_id,
                        "work_item_id": child_id,
                    }
                    await require_event_types(
                        session,
                        child_identity,
                        ["work_created", "relationship_added", "relationship_added"],
                    )
                    child_relationships = await tool(
                        session,
                        "list_relationships",
                        {**child_identity, "direction": "both"},
                    )
                    child_context = await tool(
                        session,
                        "recall_work",
                        child_identity,
                    )
                    require(
                        child_relationships["total"] == 2
                        and {item["direction"] for item in child_relationships["items"]}
                        == {"incoming", "outgoing"}
                        and all(
                            "prompt" not in item["counterpart"]
                            for item in child_relationships["items"]
                        )
                        and child_context["relationship_counts"]["incoming"] == 1
                        and child_context["relationship_counts"]["outgoing"] == 1
                        and child_context["readiness"]["is_ready"] is True,
                        "Atomic child relationships were not projected as bounded context.",
                    )

                    children = await api.get(
                        path + "/children",
                        params={"status": "pending", "limit": 100, "offset": 0},
                    )
                    require(
                        children.status_code == 200
                        and children.json()["total"] == 1
                        and children.json()["items"][0]["summary"]["work_item"]["id"] == child_id,
                        "The synthetic child was not returned by hierarchy expansion.",
                    )
                    roots = await api.get(
                        f"projects/{project_id}/work-items",
                        params={
                            "view": "roots",
                            "status": "pending",
                            "source_client": SYNTHETIC_CLIENT,
                            "source_session_id": run_id,
                            "limit": 100,
                            "offset": 0,
                        },
                    )
                    require(roots.status_code == 200, "Root hierarchy browse failed.")
                    root_ids = {
                        item["summary"]["work_item"]["id"] for item in roots.json()["items"]
                    }
                    require(
                        work_item_id in root_ids and child_id not in root_ids,
                        "Root hierarchy browse did not separate the child from its parent.",
                    )
                    root_branch = next(
                        item
                        for item in roots.json()["items"]
                        if item["summary"]["work_item"]["id"] == work_item_id
                    )
                    child_branch = children.json()["items"][0]
                    require(
                        root_branch["presentation"]
                        == {
                            "direct_child_count": 1,
                            "descendant_count": 1,
                            "blocked_descendant_count": 0,
                            "active_descendant_count": 0,
                            "completed_descendant_count": 0,
                            "discovered_descendant_count": 1,
                            "branch_unresolved_human_gate_count": 0,
                            "branch_merged_duplicate_count": 0,
                            "is_discovered_work": False,
                            "discovered_from_parent": False,
                            "next_active_descendant_lease_expires_at": None,
                        }
                        and child_branch["presentation"]
                        == {
                            "direct_child_count": 0,
                            "descendant_count": 0,
                            "blocked_descendant_count": 0,
                            "active_descendant_count": 0,
                            "completed_descendant_count": 0,
                            "discovered_descendant_count": 0,
                            "branch_unresolved_human_gate_count": 0,
                            "branch_merged_duplicate_count": 0,
                            "is_discovered_work": True,
                            "discovered_from_parent": True,
                            "next_active_descendant_lease_expires_at": None,
                        },
                        "Hierarchy browse returned incorrect root or discovery aggregates.",
                    )
                    rejected_root_query = await api.get(
                        f"projects/{project_id}/work-items",
                        params={"view": "roots", "q": child_marker},
                    )
                    require(
                        rejected_root_query.status_code == 422,
                        "A nonblank hierarchy-root query was not rejected.",
                    )

                    completion_input = {
                        "prompt": (
                            "Synthetic validation completed: exact creation, pointer search, "
                            "bounded recall, checkpoint history, dashboard edit, and conflict "
                            "detection were observed working."
                        ),
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": declared_baseline,
                        "affected_paths": initial_affected_paths,
                        "tags": [run_tag, "verification", "complete"],
                        "source_metadata": {"synthetic_check": True},
                    }
                    completion_evidence = {
                        "verification_results": [
                            {
                                "verification_type": "observation",
                                "name": "Live protocol path",
                                "outcome": "passed",
                                "summary": (
                                    "The checker observed the REST, MCP, dashboard, lease, "
                                    "graph, gate, checkpoint, event, and receipt paths used "
                                    "before this completion."
                                ),
                                "observed_at_commit": declared_baseline,
                            }
                        ],
                        "artifact_references": [
                            {
                                "artifact_type": "commit",
                                "label": "Operator-inspected repository baseline",
                                "reference": declared_baseline,
                            }
                        ],
                    }
                    completion_arguments = retained_mutation(
                        {
                            **identity,
                            "expected_version": current["version"],
                            "checkpoint": completion_input,
                            "lease_token": lease_token,
                            "completion_evidence": completion_evidence,
                            "job_completion_report": await synthetic_job_report(
                                session, project_id,
                                "The disposable integration check completed its first pass through "
                                "the application. Work history, coordination and retry checks passed "
                                "up to this point. This test does not approve or deploy product changes.",
                                ["The test leaves its immutable history available for later inspection."],
                            ),
                        }
                    )
                    completion = await protected_tool(
                        session,
                        "complete_work",
                        completion_arguments,
                    )
                    require(
                        completion["work_item"]["status"] == "done"
                        and completion["checkpoint"]["kind"] == "completion"
                        and completion["checkpoint"]["prompt"] == completion_input["prompt"]
                        and completion["checkpoint"]["affected_paths"]
                        == initial_affected_paths,
                        "Completion did not atomically save its checkpoint and done status.",
                    )
                    require_job_report(
                        completion, completion_arguments["job_completion_report"], work_item_id, "done"
                    )
                    returned_evidence = require_completion_evidence(
                        completion, completion_evidence
                    )
                    completion_replay = await protected_tool(
                        session,
                        "complete_work",
                        completion_arguments,
                    )
                    require(
                        completion_replay == completion,
                        "Same-key completion replay changed its evidence response.",
                    )
                    completed_timeline = await tool(
                        session,
                        "list_checkpoints",
                        {**identity, "order": "oldest"},
                    )
                    require(
                        completed_timeline["total"] == 3
                        and [
                            checkpoint["affected_paths"]
                            for checkpoint in completed_timeline["items"]
                        ]
                        == [
                            initial_affected_paths,
                            initial_affected_paths,
                            initial_affected_paths,
                        ],
                        "Completed checkpoint history lost exact dependency declarations.",
                    )
                    completion_events = await require_event_types(
                        session,
                        identity,
                        [
                            "work_created",
                            "progress",
                            "checkpoint_added",
                            "work_updated",
                            "work_claimed",
                            "dependency_added",
                            "work_released",
                            "dependency_removed",
                            "work_claimed",
                            "relationship_added",
                            "relationship_added",
                            "work_completed",
                        ],
                    )
                    completion_history = await tool(
                        session,
                        "list_completion_evidence",
                        {**identity, "limit": 10},
                    )
                    require(
                        completion_history["work_item_id"] == work_item_id
                        and completion_history["work_version"]
                        == completion["work_item"]["version"]
                        and completion_history["lifecycle_status"] == "done"
                        and completion_history["is_duplicate"] is False
                        and completion_history["canonical_work_item_id"] == work_item_id
                        and completion_history["current_completion_checkpoint_id"]
                        == completion["checkpoint"]["id"]
                        and completion_history["as_of_completion_event_id"]
                        == str(completion_events[-1]["id"])
                        and completion_history["total"] == 1
                        and completion_history["structured_completion_total"] == 1
                        and completion_history["limit"] == 10
                        and completion_history["next_cursor"] is None
                        and len(completion_history["items"]) == 1,
                        "MCP completion-evidence history returned incoherent episode identity.",
                    )
                    completion_episode = completion_history["items"][0]
                    require(
                        completion_episode["completion_event_id"]
                        == str(completion_events[-1]["id"])
                        and completion_episode["completion_checkpoint"]["id"]
                        == completion["checkpoint"]["id"]
                        and "prompt" not in completion_episode["completion_checkpoint"]
                        and "affected_paths"
                        not in completion_episode["completion_checkpoint"]
                        and completion_episode["verification_results"]
                        == returned_evidence["verification_results"]
                        and completion_episode["artifact_references"]
                        == returned_evidence["artifact_references"],
                        "MCP completion history changed evidence or widened its pointer.",
                    )
                    dashboard_history = await public.get(
                        proxy
                        + f"projects/{project_id}/work-items/{work_item_id}/"
                        "completion-evidence?limit=10"
                    )
                    dashboard_cache = dashboard_history.headers.get("cache-control", "")
                    require(
                        dashboard_history.status_code == 200
                        and dashboard_history.json() == completion_history
                        and "no-store" in dashboard_cache
                        and "no-transform" in dashboard_cache
                        and dashboard_history.headers.get("x-content-type-options")
                        == "nosniff"
                        and dashboard_history.headers.get("content-encoding")
                        == "identity",
                        "Dashboard evidence proxy changed, encoded, or cached the strict page.",
                    )
                    lease_tokens.pop(work_item_id, None)
                    claim_request_ids.pop(work_item_id, None)
                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": primary_marker},
                    )
                    require(
                        found["total"] == 0,
                        "Completed work remained in default-Pending search.",
                    )
                    found = await tool(
                        session,
                        "search_work",
                        {
                            "project_id": project_id,
                            "q": primary_marker,
                            "status": "all",
                        },
                    )
                    require(
                        found["total"] == 1,
                        "Completed work was lost from explicit history.",
                    )
                    require(
                        "affected_paths" not in json.dumps(found, sort_keys=True),
                        "Search pointer projections exposed checkpoint dependency declarations.",
                    )

                    for relationship_id in sorted(active_relationship_ids):
                        relationship_cleanup_arguments = retained_mutation(
                            {
                                "project_id": project_id,
                                "relationship_id": relationship_id,
                                **mutation_actor(run_id),
                            }
                        )
                        removed = await protected_tool(
                            session,
                            "remove_relationship",
                            relationship_cleanup_arguments,
                        )
                        require(
                            removed["removed"] is True
                            and removed["relationship_id"] == relationship_id,
                            "A synthetic relationship was not removed before work deletion.",
                        )
                        active_relationship_ids.remove(relationship_id)

                    await require_event_types(
                        session,
                        child_identity,
                        [
                            "work_created",
                            "relationship_added",
                            "relationship_added",
                            "relationship_removed",
                            "relationship_removed",
                        ],
                    )

                    timeline_before_reopen = [
                        "work_created",
                        "progress",
                        "checkpoint_added",
                        "work_updated",
                        "work_claimed",
                        "dependency_added",
                        "work_released",
                        "dependency_removed",
                        "work_claimed",
                        "relationship_added",
                        "relationship_added",
                        "work_completed",
                        "relationship_removed",
                        "relationship_removed",
                    ]
                    await require_event_types(session, identity, timeline_before_reopen)
                    reopen_arguments = retained_mutation(
                        {
                            **identity,
                            "expected_version": completion["work_item"]["version"],
                            "changes": {"status": "pending"},
                            **mutation_actor(run_id),
                        }
                    )
                    reopened = await protected_tool(
                        session,
                        "update_work",
                        reopen_arguments,
                    )
                    require(
                        reopened["status"] == "pending",
                        "Completed work did not reopen through the canonical update.",
                    )
                    reopened_history = await tool(
                        session,
                        "list_completion_evidence",
                        {**identity, "limit": 10},
                    )
                    require(
                        reopened_history["lifecycle_status"] == "pending"
                        and reopened_history["work_version"] == reopened["version"]
                        and reopened_history["current_completion_checkpoint_id"] is None
                        and reopened_history["items"] == completion_history["items"]
                        and reopened_history["as_of_completion_event_id"]
                        == completion_history["as_of_completion_event_id"]
                        and reopened_history["total"] == 1
                        and reopened_history["structured_completion_total"] == 1,
                        "Reopen did not retain exact history and clear the current pointer.",
                    )
                    evidence_free_checkpoint = {
                        **completion_input,
                        "prompt": (
                            "Synthetic validation recompleted the reopened work without "
                            "structured evidence to prove honest sparse history."
                        ),
                        "tags": [run_tag, "verification", "evidence-free"],
                    }
                    evidence_free_arguments = retained_mutation(
                        {
                            **identity,
                            "expected_version": reopened["version"],
                            "checkpoint": evidence_free_checkpoint,
                            "job_completion_report": await synthetic_job_report(
                                session, project_id,
                                "The disposable task was reopened and closed again to verify that "
                                "each closeout keeps its own human summary. This second closeout "
                                "does not add structured verification evidence or deploy changes.",
                            ),
                        }
                    )
                    evidence_free_completion = await protected_tool(
                        session,
                        "complete_work",
                        evidence_free_arguments,
                    )
                    require(
                        evidence_free_completion["work_item"]["status"] == "done"
                        and evidence_free_completion["checkpoint"]["kind"] == "completion"
                        and "completion_evidence" not in evidence_free_completion,
                        "Evidence-free recompletion did not preserve the sparse response contract.",
                    )
                    require(
                        await protected_tool(
                            session,
                            "complete_work",
                            evidence_free_arguments,
                        )
                        == evidence_free_completion,
                        "Evidence-free completion replay changed its historical response.",
                    )
                    final_events = await require_event_types(
                        session,
                        identity,
                        [*timeline_before_reopen, "work_reopened", "work_completed"],
                    )
                    mixed_history = await tool(
                        session,
                        "list_completion_evidence",
                        {**identity, "limit": 10},
                    )
                    require(
                        mixed_history["lifecycle_status"] == "done"
                        and mixed_history["work_version"]
                        == evidence_free_completion["work_item"]["version"]
                        and mixed_history["current_completion_checkpoint_id"]
                        == evidence_free_completion["checkpoint"]["id"]
                        and mixed_history["as_of_completion_event_id"]
                        == str(final_events[-1]["id"])
                        and mixed_history["total"] == 2
                        and mixed_history["structured_completion_total"] == 1
                        and mixed_history["next_cursor"] is None
                        and len(mixed_history["items"]) == 2
                        and mixed_history["items"][0]["completion_checkpoint"]["id"]
                        == evidence_free_completion["checkpoint"]["id"]
                        and mixed_history["items"][0]["verification_results"] == []
                        and mixed_history["items"][0]["artifact_references"] == []
                        and mixed_history["items"][1] == completion_episode,
                        "Mixed completion history lost its empty or structured episode.",
                    )
                    recalled = await tool(
                        session,
                        "recall_work",
                        {**identity, "recent_event_limit": 20},
                    )
                    recalled_json = json.dumps(recalled, sort_keys=True)
                    require(
                        recalled["event_total"] == len(final_events),
                        "Bounded recall returned an event total inconsistent with the timeline.",
                    )
                    require(
                        recalled["omitted_event_count"] == 0,
                        "Bounded recall unexpectedly omitted a synthetic event.",
                    )
                    require(
                        recalled["pre_phase5_history_may_be_incomplete"] is False,
                        "Newly created work was incorrectly marked as partial history.",
                    )
                    require(
                        [event["id"] for event in recalled["recent_events"]]
                        == [event["id"] for event in final_events],
                        "Bounded recall event IDs did not preserve chronological timeline order.",
                    )
                    require(
                        any(
                            event["event_type"] == "progress" and event["body"] == progress_body
                            for event in recalled["recent_events"]
                        ),
                        "Bounded recall did not preserve the accepted progress body.",
                    )
                    require(
                        lease_token not in recalled_json,
                        "Bounded recall exposed a lease capability.",
                    )
                    require(
                        claim_request_id not in recalled_json,
                        "Bounded recall exposed a claim request ID.",
                    )
                    final_ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(final_ready_page)
                        == [blocker_id, ready_id, terminal_id, child_id],
                        "Final ready results did not follow priority-first order.",
                    )

                    merge_source_created = await protected_tool(
                        session,
                        "create_work",
                        retained_mutation(
                            {
                                "project_id": project_id,
                                "title": (
                                    f"Permanent synthetic duplicate alias {merge_source_marker}"
                                ),
                                "summary": synthetic_summary(marker),
                                "priority": 0,
                                "initial_checkpoint": {
                                    **checkpoint_input,
                                    "prompt": (
                                        "Permanent merge-source evidence for disposable run "
                                        f"{run_id}."
                                    ),
                                    "tags": [run_tag, "verification", "merge-alias"],
                                },
                                "initial_relationships": [],
                            }
                        ),
                    )
                    merge_destination_created = await protected_tool(
                        session,
                        "create_work",
                        retained_mutation(
                            {
                                "project_id": project_id,
                                "title": (
                                    f"Permanent synthetic canonical work {merge_destination_marker}"
                                ),
                                "summary": synthetic_summary(marker),
                                "priority": 0,
                                "initial_checkpoint": {
                                    **checkpoint_input,
                                    "prompt": (
                                        "Permanent merge-destination evidence for disposable run "
                                        f"{run_id}."
                                    ),
                                    "tags": [
                                        run_tag,
                                        "verification",
                                        "merge-canonical",
                                    ],
                                },
                                "initial_relationships": [],
                            }
                        ),
                    )
                    merge_source = merge_source_created["work_item"]
                    merge_destination = merge_destination_created["work_item"]
                    merge_source_id = merge_source["id"]
                    merge_destination_id = merge_destination["id"]
                    known_work_item_ids.update({merge_source_id, merge_destination_id})
                    merge_source_identity = {
                        "project_id": project_id,
                        "work_item_id": merge_source_id,
                    }
                    merge_destination_identity = {
                        "project_id": project_id,
                        "work_item_id": merge_destination_id,
                    }
                    merge_source_creation_events = await require_event_types(
                        session, merge_source_identity, ["work_created"]
                    )
                    merge_destination_creation_events = await require_event_types(
                        session, merge_destination_identity, ["work_created"]
                    )
                    merge_source_review = await tool(session, "recall_work", merge_source_identity)
                    merge_destination_review = await tool(
                        session, "recall_work", merge_destination_identity
                    )
                    merge_rationale = (
                        f"Irreversible synthetic duplicate proof for disposable stack run {run_id}."
                    )
                    merge_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "source_work_item_id": merge_source_id,
                            "destination_work_item_id": merge_destination_id,
                            "reviewed_source_revision": merge_source_review[
                                "merge_review_revision"
                            ],
                            "reviewed_destination_revision": merge_destination_review[
                                "merge_review_revision"
                            ],
                            "rationale": merge_rationale,
                            "merged_by_client": SYNTHETIC_CLIENT,
                            "merged_by_session_id": run_id,
                            "merged_by_model": None,
                        }
                    )
                    frozen_merge_arguments = json.dumps(
                        merge_arguments, sort_keys=True, separators=(",", ":")
                    )
                    await lose_protected_tool_response(
                        args.mcp_url,
                        auth,
                        "merge_work",
                        merge_arguments,
                    )
                    merge_result = await protected_tool(session, "merge_work", merge_arguments)
                    merge_fact = merge_result["merge"]
                    require(
                        set(merge_result)
                        == {
                            "merge",
                            "source_work_item",
                            "destination_work_item",
                            "direct_destination",
                            "canonical_work_item",
                            "supporting_relationship_created",
                            "supporting_relationship",
                            "relationship_events",
                            "merge_events",
                        }
                        and set(merge_fact)
                        == {
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
                        },
                        "Recovered merge response did not use the exact public shape.",
                    )
                    relationship = merge_result["supporting_relationship"]
                    relationship_events = merge_result["relationship_events"]
                    merge_events = merge_result["merge_events"]
                    all_merge_events = [*relationship_events, *merge_events]
                    require(
                        merge_fact["project_id"] == project_id
                        and merge_fact["source_work_item_id"] == merge_source_id
                        and merge_fact["destination_work_item_id"] == merge_destination_id
                        and merge_fact["reviewed_source_revision"]
                        == merge_source_review["merge_review_revision"]
                        and merge_fact["reviewed_destination_revision"]
                        == merge_destination_review["merge_review_revision"]
                        and merge_fact["rationale"] == merge_rationale
                        and merge_fact["merged_by_client"] == SYNTHETIC_CLIENT
                        and merge_fact["merged_by_session_id"] == run_id
                        and merge_fact["merged_by_model"] is None
                        and merge_result["source_work_item"]["version"]
                        == merge_source["version"] + 1
                        == merge_fact["resulting_source_work_version"]
                        and merge_result["destination_work_item"]["version"]
                        == merge_destination["version"] + 1
                        == merge_fact["resulting_destination_work_version"]
                        and merge_result["source_work_item"]["updated_at"]
                        == merge_fact["created_at"]
                        and merge_result["destination_work_item"]["updated_at"]
                        == merge_fact["created_at"]
                        and merge_result["direct_destination"]["id"] == merge_destination_id
                        and merge_result["canonical_work_item"]
                        == merge_result["direct_destination"],
                        "Recovered merge changed direction, revisions, versions, or timestamp.",
                    )
                    require(
                        merge_result["supporting_relationship_created"] is True
                        and relationship["id"] == merge_fact["duplicate_relationship_id"]
                        and relationship["relationship_type"] == "duplicate-of"
                        and relationship["source_work_item_id"] == merge_source_id
                        and relationship["target_work_item_id"] == merge_destination_id
                        and relationship["created_at"] == merge_fact["created_at"]
                        and len(relationship_events) == 2
                        and len(merge_events) == 2
                        and [event["work_item_id"] for event in relationship_events]
                        == [merge_source_id, merge_destination_id]
                        and [event["work_item_id"] for event in merge_events]
                        == [merge_source_id, merge_destination_id]
                        and [event["metadata"]["role"] for event in merge_events]
                        == ["source", "destination"]
                        and all(
                            event["created_at"] == merge_fact["created_at"]
                            and event["actor_client"] == SYNTHETIC_CLIENT
                            and event["actor_session_id"] == run_id
                            for event in all_merge_events
                        )
                        and all(
                            event["body"] == merge_rationale
                            and event["metadata"]
                            == {
                                "merge_id": merge_fact["id"],
                                "source_work_item_id": merge_source_id,
                                "destination_work_item_id": merge_destination_id,
                                "role": event["metadata"]["role"],
                                "source_work_version": merge_result["source_work_item"]["version"],
                                "destination_work_version": merge_result["destination_work_item"][
                                    "version"
                                ],
                            }
                            for event in merge_events
                        ),
                        "Recovered merge did not expose its exact relationship/event fact set.",
                    )
                    merge_json = json.dumps(merge_result, sort_keys=True)
                    require(
                        merge_arguments["client_operation_id"] not in merge_json
                        and key not in merge_json,
                        "Recovered merge exposed a receipt key or bearer capability.",
                    )
                    before_merge_replay = await merge_observables(
                        session, merge_source_identity, merge_destination_identity
                    )
                    source_event_ids = {
                        merge_source_creation_events[0]["id"],
                        relationship_events[0]["id"],
                        merge_events[0]["id"],
                    }
                    destination_event_ids = {
                        merge_destination_creation_events[0]["id"],
                        relationship_events[1]["id"],
                        merge_events[1]["id"],
                    }
                    require(
                        {event["id"] for event in before_merge_replay["source_events"]}
                        == source_event_ids
                        and {event["id"] for event in before_merge_replay["destination_events"]}
                        == destination_event_ids,
                        "Recovered merge events were not attached to both exact identities.",
                    )
                    merge_replay = await protected_tool(session, "merge_work", merge_arguments)
                    after_merge_replay = await merge_observables(
                        session, merge_source_identity, merge_destination_identity
                    )
                    require(
                        json.dumps(merge_arguments, sort_keys=True, separators=(",", ":"))
                        == frozen_merge_arguments
                        and merge_replay == merge_result
                        and after_merge_replay == before_merge_replay,
                        "Exact merge receipt replay changed its response or public facts.",
                    )

                    merge_source_detail = before_merge_replay["source_detail"]
                    merge_destination_detail = before_merge_replay["destination_detail"]
                    source_after_merge, source_canonical = work_detail_parts(
                        merge_source_detail, merge_source_id
                    )
                    _destination_after_merge, destination_canonical = work_detail_parts(
                        merge_destination_detail, merge_destination_id
                    )
                    require(
                        source_after_merge == merge_result["source_work_item"]
                        and source_canonical
                        == {
                            "is_duplicate": True,
                            "direct_destination": merge_result["direct_destination"],
                            "canonical_work_item": merge_result["canonical_work_item"],
                            "path": [merge_result["direct_destination"]],
                            "duplicate_member_count": 1,
                        }
                        and destination_canonical["is_duplicate"] is False
                        and destination_canonical["duplicate_member_count"] == 1,
                        "Exact alias/root reads lost identity or canonical authority.",
                    )
                    source_merge_context = before_merge_replay["source_context"]
                    require(
                        source_merge_context["work_item"]["id"] == merge_source_id
                        and source_merge_context["canonical"] == source_canonical
                        and source_merge_context["readiness"]["is_duplicate"] is True
                        and source_merge_context["readiness"]["canonical_work_item_id"]
                        == merge_destination_id
                        and source_merge_context["readiness"]["is_ready"] is False
                        and source_merge_context["readiness"]["display_state"] == "duplicate"
                        and source_merge_context["duplicate_member_total"] == 1
                        and source_merge_context["omitted_duplicate_member_count"] == 0
                        and source_merge_context["duplicate_members"]
                        == [
                            {
                                "id": merge_source_id,
                                "title": merge_source["title"],
                                "status": merge_source["status"],
                            }
                        ],
                        "Alias recall did not retain its audit identity and root authority.",
                    )

                    canonical_match = await tool(
                        session,
                        "search_work",
                        {
                            "project_id": project_id,
                            "q": merge_source_marker,
                            "status": "all",
                        },
                    )
                    alias_match = await tool(
                        session,
                        "search_work",
                        {
                            "project_id": project_id,
                            "q": merge_source_marker,
                            "status": "all",
                            "duplicate_scope": "aliases",
                        },
                    )
                    require(
                        canonical_match["total"] == 1
                        and canonical_match["items"][0]["summary"]["work_item"]["id"]
                        == merge_destination_id
                        and canonical_match["items"][0]["matched_member"]["id"] == merge_source_id
                        and alias_match["total"] == 1
                        and alias_match["items"][0]["summary"]["work_item"]["id"] == merge_source_id
                        and alias_match["items"][0]["matched_member"]["id"] == merge_source_id,
                        "Canonical search or explicit alias audit confused row and match IDs.",
                    )
                    merged_roots = await tool(
                        session,
                        "search_work",
                        {
                            "project_id": project_id,
                            "status": "all",
                            "view": "roots",
                            "source_client": SYNTHETIC_CLIENT,
                            "source_session_id": run_id,
                            "limit": 100,
                        },
                    )
                    merged_root_ids = {
                        item["summary"]["work_item"]["id"] for item in merged_roots["items"]
                    }
                    merged_destination_branch = next(
                        item
                        for item in merged_roots["items"]
                        if item["summary"]["work_item"]["id"] == merge_destination_id
                    )
                    require(
                        merge_source_id not in merged_root_ids
                        and merge_destination_id in merged_root_ids
                        and merged_destination_branch["presentation"][
                            "branch_merged_duplicate_count"
                        ]
                        == 1,
                        "Hierarchy did not omit the alias or count retained merge evidence.",
                    )

                    rejected_alias_update = await api.patch(
                        f"projects/{project_id}/work-items/{merge_source_id}",
                        json=retained_mutation(
                            {
                                "expected_version": source_after_merge["version"],
                                "title": "Forbidden post-merge synthetic edit",
                                "actor": mutation_actor(run_id),
                            }
                        ),
                    )
                    after_rejected_alias_update = await merge_observables(
                        session, merge_source_identity, merge_destination_identity
                    )
                    require(
                        rejected_alias_update.status_code == 409
                        and typed_error_code(rejected_alias_update) == "work_duplicate"
                        and rejected_alias_update.json()["detail"]["context"]
                        == {"canonical_work_item_id": merge_destination_id}
                        and after_rejected_alias_update == before_merge_replay,
                        "Fresh alias mutation did not fail closed with zero public effects.",
                    )

                    rejected_alias_claim = await api.post(
                        f"projects/{project_id}/work-items/{merge_source_id}/claim",
                        json={
                            "holder_client": SYNTHETIC_CLIENT,
                            "holder_session_id": run_id,
                            "claim_request_id": str(uuid4()),
                        },
                        follow_redirects=False,
                    )
                    rejected_alias_claim_detail = rejected_alias_claim.json().get("detail", {})
                    after_rejected_alias_claim = await merge_observables(
                        session, merge_source_identity, merge_destination_identity
                    )
                    require(
                        rejected_alias_claim.status_code == 409
                        and rejected_alias_claim.headers.get("location") is None
                        and set(rejected_alias_claim_detail) == {"code", "message", "context"}
                        and rejected_alias_claim_detail["code"] == "work_duplicate"
                        and rejected_alias_claim_detail["context"]
                        == {"canonical_work_item_id": merge_destination_id}
                        and merge_source_id
                        not in json.dumps(rejected_alias_claim_detail["context"], sort_keys=True)
                        and after_rejected_alias_claim == before_merge_replay,
                        "Exact alias claim redirected, widened context, or changed merge facts.",
                    )

                    await phase12_human_report_flow(
                        session, api, project_id, terminal_identity, run_id, checkpoint_input,
                        known_work_item_ids, activity_start,
                    )
                    synthetic_ids = (
                        child_id,
                        terminal_id,
                        ready_id,
                        blocker_id,
                        work_item_id,
                    )
                    for item_id in synthetic_ids:
                        item_identity = {
                            "project_id": project_id,
                            "work_item_id": item_id,
                        }
                        latest_detail = await tool(session, "get_work", item_identity)
                        latest, _canonical = work_detail_parts(latest_detail, item_id)
                        delete_arguments = retained_mutation(
                            {
                                **item_identity,
                                "expected_version": latest["version"],
                                **mutation_actor(run_id),
                            }
                        )
                        deleted = await protected_tool(
                            session,
                            "delete_work",
                            delete_arguments,
                        )
                        require(
                            deleted["deleted"] is True and deleted["work_item_id"] == item_id,
                            "Canonical delete did not return its explicit receipt.",
                        )

                    for item_id in synthetic_ids:
                        require(
                            (
                                await api.get(f"projects/{project_id}/work-items/{item_id}")
                            ).status_code
                            == 404,
                            "Soft-deleted synthetic work remains readable.",
                        )
                    preserved_report = await tool(session, "get_job_completion_report", {
                        "project_id": project_id,
                        "report_id": completion["job_completion_report"]["id"],
                    })
                    require(
                        preserved_report["source_work_state"]["deleted"] is True
                        and preserved_report["report"]["summary"]
                        == completion["job_completion_report"]["summary"],
                        "Soft deletion lost the exact immutable closeout report.",
                    )
                    print(
                        "PASS: Phase 12 activity resume, all three report closeouts, human "
                        "dismissal/replay and pending follow-up with dual provenance; "
                        "canonical create/suggest/search/recall/checkpoints/events, "
                        "resource/prompt, dashboard edit, typed stale conflict, "
                        "claim/replay/renew/release, pointer and capability isolation, "
                        "human-gate request/resolution replay, single activity advance, "
                        "attention/history/context convergence under ordinary-event pressure, "
                        "waiting readiness and exact hierarchy counts, event replay/no-op "
                        "behavior, atomic child/discovery, hierarchy browse, leased completion/"
                        "reopen, irreversible merge response-loss recovery, exact receipt replay, "
                        "alias authority/freeze, and reversible graph deletion"
                    )
                finally:
                    preserved = await cleanup_synthetic_work(
                        api,
                        project_id,
                        marker,
                        run_id,
                        known_work_item_ids,
                        known_relationship_ids,
                        claim_request_ids,
                        lease_tokens,
                    )
                    report_retained_merge_evidence(preserved, project_id)


def validate_cli_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Enforce mutually consistent read-only, writable, and cleanup modes."""
    if args.other_project_id:
        if not args.project_id:
            parser.error("--other-project-id requires --project-id.")
        if args.other_project_id == args.project_id:
            parser.error("--other-project-id must identify a different project.")
    if args.cleanup_run_id and not args.project_id:
        parser.error("--cleanup-run-id requires --project-id.")
    if args.cleanup_run_id and args.other_project_id:
        parser.error("--cleanup-run-id cannot be combined with --other-project-id.")
    validate_repository_cli_arguments(parser, args)


def validate_repository_cli_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Validate repository declarations only for a writable non-cleanup run."""
    scope_supplied = args.verified_against is not None or args.affected_paths is not None
    if scope_supplied and not args.project_id:
        parser.error("Repository declaration arguments require --project-id.")
    if args.cleanup_run_id and scope_supplied:
        parser.error("Cleanup does not accept repository declaration arguments.")
    if args.project_id and not args.cleanup_run_id:
        if args.verified_against is None or not args.affected_paths:
            parser.error(
                "Writable Phase 12 checks require --verified-against and at least one "
                "--affected-path from the repository actually inspected by the operator."
            )
        try:
            args.verified_against, args.affected_paths = validated_repository_scope(
                args.verified_against,
                args.affected_paths,
            )
        except argparse.ArgumentTypeError as error:
            parser.error(str(error))


def main() -> None:
    values = local_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=f"http://127.0.0.1:{values.get('MNEMONIC_API_PORT', '8000')}",
    )
    parser.add_argument(
        "--mcp-url",
        default=f"http://127.0.0.1:{values.get('MNEMONIC_MCP_PORT', '8001')}/mcp",
    )
    parser.add_argument(
        "--web-url",
        default=f"http://127.0.0.1:{values.get('MNEMONIC_WEB_PORT', '3000')}",
    )
    parser.add_argument(
        "--project-id",
        type=project_uuid,
        help=(
            "Explicitly authorizes a disposable Phase 12 writable lifecycle, including an "
            "irreversible two-item merge whose evidence is permanently retained"
        ),
    )
    parser.add_argument(
        "--other-project-id",
        type=project_uuid,
        help="Optional second project for an isolation check",
    )
    parser.add_argument(
        "--verified-against",
        type=full_commit_oid,
        help=(
            "Full commit object ID actually inspected for the writable check's repository "
            "declaration"
        ),
    )
    parser.add_argument(
        "--affected-path",
        action="append",
        dest="affected_paths",
        metavar="PATTERN",
        help=(
            "Ordered repository-relative dependency pattern actually inspected for the writable "
            "check; repeat for multiple patterns"
        ),
    )
    parser.add_argument(
        "--cleanup-run-id",
        type=project_uuid,
        help=(
            "Clean reversible synthetic marker data from an interrupted run; retained merge "
            "evidence cannot be removed; retry after lease expiry if its token was lost"
        ),
    )
    args = parser.parse_args()
    validate_cli_arguments(parser, args)
    key = values.get("MNEMONIC_API_KEY", "")
    if len(key) < 32:
        parser.error("Set MNEMONIC_API_KEY in .env or the environment first.")
    asyncio.run(check(args, key))


if __name__ == "__main__":
    main()
