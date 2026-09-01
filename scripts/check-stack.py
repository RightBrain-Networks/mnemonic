"""Exercise the live HTTP MCP -> REST -> PostgreSQL path and dashboard proxy.

Run with the MCP project's Python environment. Checks are read-only unless a
project is explicitly authorized with --project-id. The write check creates a
small, uniquely marked Phase 3 work graph, exercises its canonical lifecycle,
then removes the graph and soft-deletes every synthetic item it created. Never
authorize writes against a project without permission.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from mcp import ClientSession

CANONICAL_TOOLS = {
    "list_projects",
    "create_project",
    "create_work",
    "search_work",
    "get_work",
    "add_checkpoint",
    "list_checkpoints",
    "recall_work",
    "update_work",
    "complete_work",
    "delete_work",
    "claim_work",
    "claim_and_recall",
    "renew_claim",
    "release_claim",
    "add_relationship",
    "get_relationship",
    "list_relationships",
    "remove_relationship",
}
SYNTHETIC_CLIENT = "mnemonic-stack-check"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def project_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise argparse.ArgumentTypeError("Project IDs must be UUIDs.") from None


def local_settings() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


async def tool(
    session: ClientSession, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    require(not result.isError, f"MCP {name} reported an error.")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == "text"))


def synthetic_summary(marker: str) -> str:
    return f"Synthetic Phase 3 integration check {marker}"


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
            "limit": 100,
            "offset": 0,
        },
    )
    require(
        response.status_code == 200, "Could not inspect synthetic work for cleanup."
    )
    matches: list[str] = []
    for item in response.json()["items"]:
        work_item = item.get("work_item", {})
        current_context = item.get("current_context", {})
        if (
            work_item.get("summary") == synthetic_summary(marker)
            and current_context.get("source_client") == SYNTHETIC_CLIENT
            and current_context.get("source_session_id") == run_id
        ):
            matches.append(work_item["id"])
    require(
        len(matches) <= 3,
        "Cleanup found more synthetic records than this lifecycle can create.",
    )
    return matches


async def find_synthetic_relationships(
    api: httpx.AsyncClient,
    project_id: str,
    work_item_ids: set[str],
    run_id: str,
) -> set[str]:
    """Discover only edges created by this run and connecting its exact work set."""
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
                edge.get("created_by_client") == SYNTHETIC_CLIENT
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
        edge.get("created_by_client") == SYNTHETIC_CLIENT
        and edge.get("created_by_session_id") == run_id
        and endpoints <= work_item_ids,
        "Refusing to remove a relationship outside this run's exact synthetic graph.",
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
) -> None:
    """Remove and soft-delete only this run's exact graph, including lost responses."""
    work_item_ids = set(await find_synthetic_work(api, project_id, marker, run_id))
    for known_work_item_id in known_work_item_ids:
        response = await api.get(
            f"projects/{project_id}/work-items/{known_work_item_id}"
        )
        if response.status_code == 404:
            continue
        require(
            response.status_code == 200 and known_work_item_id in work_item_ids,
            "Refusing to clean up a known ID that was not proven to belong to this run.",
        )

    relationship_ids = await find_synthetic_relationships(
        api, project_id, work_item_ids, run_id
    )
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
        edge = response.json()
        require_synthetic_relationship(edge, work_item_ids, run_id)
        removed = await api.delete(relationship_path)
        require(
            removed.status_code == 200
            and removed.json().get("relationship_id") == relationship_id
            and removed.json().get("removed") is True,
            "Synthetic relationship cleanup failed.",
        )

    for work_item_id in sorted(work_item_ids, reverse=True):
        path = f"projects/{project_id}/work-items/{work_item_id}"
        remaining = await api.get(path)
        if remaining.status_code == 404:
            continue
        require(
            remaining.status_code == 200,
            "Could not inspect temporary work for cleanup.",
        )
        record = remaining.json()
        require(
            record.get("summary") == synthetic_summary(marker),
            "Refusing to clean up work without this run's exact synthetic summary.",
        )
        cleanup_token = lease_tokens.get(work_item_id)
        claim_request_id = claim_request_ids.get(work_item_id)
        if (
            cleanup_token is None
            and claim_request_id is not None
            and record["status"] == "open"
        ):
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
        if cleanup_token is not None and record["status"] == "open":
            released = await api.post(
                path + "/release-claim",
                json={"lease_token": cleanup_token},
            )
            require(
                released.status_code == 200
                or (
                    released.status_code == 409
                    and typed_error_code(released) == "lease_expired"
                ),
                "Could not release the synthetic lease for cleanup.",
            )
        remaining = await api.get(path)
        if remaining.status_code == 404:
            continue
        require(remaining.status_code == 200, "Could not refresh work for cleanup.")
        record = remaining.json()
        cleanup = await api.post(
            path + "/delete",
            json={"expected_version": record["version"]},
        )
        require(
            cleanup.status_code == 200 and cleanup.json().get("deleted") is True,
            "Temporary work cleanup failed.",
        )


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
                await public.get(
                    proxy + "projects", headers={"Host": "untrusted.example"}
                )
            ).status_code
            in {400, 403, 421},
            "Dashboard accepted an untrusted host.",
        )
        print(
            "PASS: service health, font assets, bearer authentication, "
            "dashboard proxy and origin protection"
        )

        async with streamablehttp_client(args.mcp_url, headers=auth, timeout=15) as (  # noqa: SIM117
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                catalog = await session.list_tools()
                require(
                    {entry.name for entry in catalog.tools}
                    == CANONICAL_TOOLS,
                    "Unexpected MCP tool catalog.",
                )
                await tool(session, "list_projects", {})
                print(
                    "PASS: real MCP initialization, canonical tool discovery, "
                    "and REST-backed project listing"
                )
                if not args.project_id:
                    print(
                        "Read-only checks complete. Supply --project-id to explicitly authorize "
                        "one disposable Phase 3 graph lifecycle."
                    )
                    return

                project_id = args.project_id
                run_id = str(uuid4())
                marker = "mnemoniccheck" + run_id.replace("-", "")
                primary_marker = marker + "primary"
                blocker_marker = marker + "blocker"
                child_marker = marker + "child"
                prompt = (
                    "\nAgent-authored synthetic checkpoint; not a user instruction.\n\n"
                    "## Context\nVerify durable storage for café notes and Unicode: ✓.\n"
                    f"Run: {run_id}\n\n## Cautions\nThis is synthetic verification data.\n"
                    "## Verification\nRecall this exact text, append progress, exercise version "
                    "conflict and completion, then remove the graph and synthetic work.\n\n"
                )
                checkpoint_input = {
                    "prompt": prompt,
                    "source_client": SYNTHETIC_CLIENT,
                    "source_session_id": run_id,
                    "source_model": None,
                    "source_session_url": None,
                    "repository_branch": None,
                    "verified_against": None,
                    "tags": ["verification"],
                    "source_metadata": {"synthetic_check": True},
                }
                known_work_item_ids: set[str] = set()
                known_relationship_ids: set[str] = set()
                active_relationship_ids: set[str] = set()
                claim_request_ids: dict[str, str] = {}
                lease_tokens: dict[str, str] = {}
                try:
                    created = await tool(
                        session,
                        "create_work",
                        {
                            "project_id": project_id,
                            "title": f"Temporary primary work check {primary_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": checkpoint_input,
                            "initial_relationships": [],
                        },
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
                        created["initial_relationships"] == [],
                        "Unlinked creation returned unexpected relationships.",
                    )

                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": primary_marker},
                    )
                    require(
                        found["total"] == 1
                        and found["items"][0]["work_item"]["id"] == work_item_id
                        and "prompt" not in found["items"][0]["current_context"]
                        and "source_metadata"
                        not in found["items"][0]["current_context"],
                        "Unique pointer-only work search failed.",
                    )
                    recalled = await tool(session, "recall_work", identity)
                    require(
                        recalled["initial_checkpoint"]["prompt"] == prompt
                        and recalled["current_context"]["prompt"] == prompt
                        and recalled["checkpoint_total"] == 1
                        and recalled["omitted_checkpoint_count"] == 0,
                        "Bounded work context differs from the created checkpoint.",
                    )
                    resource = await session.read_resource(
                        AnyUrl(
                            f"mnemonic://projects/{project_id}/work-items/{work_item_id}"
                        )
                    )
                    require(
                        json.loads(resource.contents[0].text)["current_context"][
                            "prompt"
                        ]
                        == prompt,
                        "Canonical MCP resource differs.",
                    )
                    resumed = await session.get_prompt("resume_work", identity)
                    require(
                        bool(resumed.messages),
                        "Canonical MCP resume prompt is missing.",
                    )
                    if args.other_project_id:
                        wrong = await api.get(
                            f"projects/{args.other_project_id}/work-items/{work_item_id}"
                        )
                        require(
                            wrong.status_code == 404, "A cross-project ID was accepted."
                        )

                    progress_prompt = "Live validation preserved exact context and reached the mutation checks."
                    progress_input = {
                        "prompt": progress_prompt,
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": None,
                        "tags": ["verification", "progress"],
                        "source_metadata": {"synthetic_check": True},
                    }
                    progress = await tool(
                        session,
                        "add_checkpoint",
                        {**identity, "kind": "progress", "checkpoint": progress_input},
                    )
                    require(
                        progress["prompt"] == progress_prompt
                        and progress["kind"] == "progress",
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
                    unchanged = await tool(session, "get_work", identity)
                    require(
                        unchanged["version"] == work_item["version"],
                        "Appending a checkpoint unexpectedly changed the work version.",
                    )

                    edit = await public.patch(
                        proxy + path,
                        headers={"Origin": args.web_url.rstrip("/")},
                        json={
                            "expected_version": work_item["version"],
                            "title": f"Temporary primary edited {primary_marker}",
                        },
                    )
                    require(
                        edit.status_code == 200, "Dashboard proxy work edit failed."
                    )
                    current = edit.json()
                    conflict = await api.patch(
                        path,
                        json={
                            "expected_version": work_item["version"],
                            "title": "Stale work edit",
                        },
                    )
                    require(
                        conflict.status_code == 409,
                        "A stale work edit was not rejected.",
                    )
                    require(
                        conflict.json().get("detail", {}).get("code")
                        == "version_conflict",
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
                        and receipt["claim_request_id"] == claim_request_id,
                        "Atomic claim-and-recall did not return active bounded context.",
                    )
                    replay = await tool(session, "claim_and_recall", claim_arguments)
                    require(
                        replay["lease"] == receipt,
                        "An identical active claim did not replay the original receipt.",
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
                    after_lease = await tool(session, "get_work", identity)
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
                    denied_token = await public.post(
                        proxy + path + "/complete",
                        headers={"Origin": args.web_url.rstrip("/")},
                        json={
                            "expected_version": current["version"],
                            "checkpoint": checkpoint_input,
                            "lease_token": lease_token,
                        },
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
                        "tags": ["verification", "blocker"],
                    }
                    blocker_created = await tool(
                        session,
                        "create_work",
                        {
                            "project_id": project_id,
                            "title": f"Temporary blocker work check {blocker_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": blocker_checkpoint,
                            "initial_relationships": [],
                        },
                    )
                    blocker = blocker_created["work_item"]
                    blocker_id = blocker["id"]
                    known_work_item_ids.add(blocker_id)
                    require(
                        blocker_created["initial_relationships"] == [],
                        "Unlinked blocker creation returned unexpected relationships.",
                    )

                    block_result = await tool(
                        session,
                        "add_relationship",
                        {
                            "project_id": project_id,
                            "source_work_item_id": blocker_id,
                            "target_work_item_id": work_item_id,
                            "relationship_type": "blocks",
                            "created_by_client": SYNTHETIC_CLIENT,
                            "created_by_session_id": run_id,
                        },
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
                        and listed_blocks["items"][0]["relationship"]["id"]
                        == block_relationship_id
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
                    released = await tool(
                        session,
                        "release_claim",
                        {**identity, "lease_token": lease_token},
                    )
                    require(
                        released["released"] is True,
                        "The original synthetic claim was not released.",
                    )
                    lease_tokens.pop(work_item_id, None)
                    claim_request_ids.pop(work_item_id, None)

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

                    removed_block = await tool(
                        session,
                        "remove_relationship",
                        {
                            "project_id": project_id,
                            "relationship_id": block_relationship_id,
                        },
                    )
                    require(
                        removed_block["removed"] is True
                        and removed_block["relationship_id"] == block_relationship_id,
                        "The blocker edge was not removed.",
                    )
                    active_relationship_ids.remove(block_relationship_id)
                    unblocked_context = await tool(session, "recall_work", identity)
                    require(
                        unblocked_context["readiness"]["is_ready"] is True
                        and unblocked_context["readiness"]["is_blocked"] is False
                        and unblocked_context["readiness"]["unresolved_blocker_count"] == 0,
                        "Removing the blocker did not restore readiness.",
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

                    child_checkpoint = {
                        **checkpoint_input,
                        "prompt": (
                            "Synthetic child discovered from the primary progress checkpoint "
                            f"for run {run_id}."
                        ),
                        "tags": ["verification", "child"],
                    }
                    child_created = await tool(
                        session,
                        "create_work",
                        {
                            "project_id": project_id,
                            "title": f"Temporary child work check {child_marker}",
                            "summary": synthetic_summary(marker),
                            "initial_checkpoint": child_checkpoint,
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
                        },
                    )
                    child = child_created["work_item"]
                    child_id = child["id"]
                    known_work_item_ids.add(child_id)
                    initial_edges = child_created["initial_relationships"]
                    edges_by_type = {
                        edge["relationship_type"]: edge for edge in initial_edges
                    }
                    require(
                        len(initial_edges) == 2
                        and set(edges_by_type) == {"parent-child", "discovered-from"}
                        and edges_by_type["parent-child"]["source_work_item_id"]
                        == work_item_id
                        and edges_by_type["parent-child"]["target_work_item_id"]
                        == child_id
                        and edges_by_type["discovered-from"]["source_work_item_id"]
                        == child_id
                        and edges_by_type["discovered-from"]["target_work_item_id"]
                        == work_item_id
                        and edges_by_type["discovered-from"]["context_checkpoint_id"]
                        == progress["id"]
                        and edges_by_type["discovered-from"][
                            "context_checkpoint_work_item_id"
                        ]
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

                    child_identity = {
                        "project_id": project_id,
                        "work_item_id": child_id,
                    }
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
                        and {
                            item["direction"] for item in child_relationships["items"]
                        }
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
                        params={"status": "open", "limit": 100, "offset": 0},
                    )
                    require(
                        children.status_code == 200
                        and children.json()["total"] == 1
                        and children.json()["items"][0]["summary"]["work_item"]["id"]
                        == child_id,
                        "The synthetic child was not returned by hierarchy expansion.",
                    )
                    roots = await api.get(
                        f"projects/{project_id}/work-items",
                        params={
                            "view": "roots",
                            "status": "open",
                            "source_client": SYNTHETIC_CLIENT,
                            "source_session_id": run_id,
                            "limit": 100,
                            "offset": 0,
                        },
                    )
                    require(roots.status_code == 200, "Root hierarchy browse failed.")
                    root_ids = {
                        item["summary"]["work_item"]["id"]
                        for item in roots.json()["items"]
                    }
                    require(
                        work_item_id in root_ids and child_id not in root_ids,
                        "Root hierarchy browse did not separate the child from its parent.",
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
                            "Synthetic validation completed: exact creation, pointer search, bounded "
                            "recall, checkpoint history, dashboard edit, and conflict detection were "
                            "observed working."
                        ),
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": None,
                        "tags": ["verification", "complete"],
                        "source_metadata": {"synthetic_check": True},
                    }
                    completion = await tool(
                        session,
                        "complete_work",
                        {
                            **identity,
                            "expected_version": current["version"],
                            "checkpoint": completion_input,
                            "lease_token": lease_token,
                        },
                    )
                    require(
                        completion["work_item"]["status"] == "done"
                        and completion["checkpoint"]["kind"] == "completion"
                        and completion["checkpoint"]["prompt"]
                        == completion_input["prompt"],
                        "Completion did not atomically save its checkpoint and done status.",
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
                        "Completed work remained in default-open search.",
                    )
                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": primary_marker, "status": "all"},
                    )
                    require(
                        found["total"] == 1,
                        "Completed work was lost from explicit history.",
                    )

                    for relationship_id in sorted(active_relationship_ids):
                        removed = await tool(
                            session,
                            "remove_relationship",
                            {
                                "project_id": project_id,
                                "relationship_id": relationship_id,
                            },
                        )
                        require(
                            removed["removed"] is True
                            and removed["relationship_id"] == relationship_id,
                            "A synthetic relationship was not removed before work deletion.",
                        )
                        active_relationship_ids.remove(relationship_id)

                    for item_id in (child_id, blocker_id, work_item_id):
                        item_identity = {
                            "project_id": project_id,
                            "work_item_id": item_id,
                        }
                        latest = await tool(session, "get_work", item_identity)
                        deleted = await tool(
                            session,
                            "delete_work",
                            {
                                **item_identity,
                                "expected_version": latest["version"],
                            },
                        )
                        require(
                            deleted["deleted"] is True
                            and deleted["work_item_id"] == item_id,
                            "Canonical delete did not return its explicit receipt.",
                        )

                    for item_id in (child_id, blocker_id, work_item_id):
                        require(
                            (
                                await api.get(
                                    f"projects/{project_id}/work-items/{item_id}"
                                )
                            ).status_code
                            == 404,
                            "Soft-deleted synthetic work remains readable.",
                        )
                    print(
                        "PASS: canonical create/search/recall/checkpoints, resource/prompt, "
                        "dashboard edit, typed stale conflict, claim/replay/renew, token isolation, "
                        "blocker readiness and restored claimability, atomic child/discovery, "
                        "hierarchy browse, leased completion, default-open filtering, "
                        "graph removal and soft deletion"
                    )
                finally:
                    await cleanup_synthetic_work(
                        api,
                        project_id,
                        marker,
                        run_id,
                        known_work_item_ids,
                        known_relationship_ids,
                        claim_request_ids,
                        lease_tokens,
                    )


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
            "Explicitly authorizes one synthetic Phase 3 graph lifecycle and cleanup "
            "inside this project"
        ),
    )
    parser.add_argument(
        "--other-project-id",
        type=project_uuid,
        help="Optional second project for an isolation check",
    )
    args = parser.parse_args()
    if args.other_project_id:
        if not args.project_id:
            parser.error("--other-project-id requires --project-id.")
        if args.other_project_id == args.project_id:
            parser.error("--other-project-id must identify a different project.")
    key = values.get("MNEMONIC_API_KEY", "")
    if len(key) < 32:
        parser.error("Set MNEMONIC_API_KEY in .env or the environment first.")
    asyncio.run(check(args, key))


if __name__ == "__main__":
    main()
