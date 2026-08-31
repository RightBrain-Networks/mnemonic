"""Exercise the live HTTP MCP -> REST -> PostgreSQL path and dashboard proxy.

Run with the MCP project's Python environment. Checks are read-only unless a
project is explicitly authorized with --project-id. The write check creates one
uniquely marked work item, exercises the Phase 2 lifecycle, and soft-deletes it.
Never authorize writes against a project without permission.
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

CANONICAL_AND_COMPATIBILITY_TOOLS = {
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
    "save_handoff",
    "search_handoffs",
    "recall_handoff",
    "list_handoff_comments",
    "add_handoff_comment",
    "complete_handoff",
    "update_handoff",
    "delete_handoff",
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


async def find_synthetic_work(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    run_id: str,
) -> list[str]:
    """Find only this run's exact synthetic work after an uncertain create response."""
    response = await api.get(
        f"projects/{project_id}/work-items",
        params={"q": marker, "status": "all", "limit": 100, "offset": 0},
    )
    require(
        response.status_code == 200, "Could not inspect synthetic work for cleanup."
    )
    matches: list[str] = []
    for item in response.json()["items"]:
        work_item = item.get("work_item", {})
        current_context = item.get("current_context", {})
        if (
            work_item.get("title") == f"Temporary work check {marker}"
            and work_item.get("summary") == f"Synthetic integration check {marker}"
            and current_context.get("source_client") == SYNTHETIC_CLIENT
            and current_context.get("source_session_id") == run_id
        ):
            matches.append(work_item["id"])
    require(len(matches) <= 1, "Cleanup found multiple synthetic records for one run.")
    return matches


async def cleanup_synthetic_work(
    api: httpx.AsyncClient,
    project_id: str,
    marker: str,
    run_id: str,
    known_work_item_id: str | None,
    claim_request_id: str | None,
    lease_token: str | None,
) -> None:
    """Soft-delete the exact synthetic item, including after an uncertain create."""
    work_item_ids = (
        [known_work_item_id]
        if known_work_item_id is not None
        else await find_synthetic_work(api, project_id, marker, run_id)
    )
    for work_item_id in work_item_ids:
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
            marker in record.get("title", "") or marker in record.get("summary", ""),
            "Refusing to clean up work that lacks this run's unique marker.",
        )
        cleanup_token = lease_token
        if cleanup_token is None and claim_request_id is not None and record["status"] == "open":
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
                    and recovered.json().get("detail", {}).get("code")
                    == "claim_request_expired",
                    "Could not recover the synthetic lease for cleanup.",
                )
        if cleanup_token is not None:
            released = await api.post(
                path + "/release-claim",
                json={"lease_token": cleanup_token},
            )
            require(
                released.status_code == 200,
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
                    == CANONICAL_AND_COMPATIBILITY_TOOLS,
                    "Unexpected MCP tool catalog.",
                )
                await tool(session, "list_projects", {})
                print(
                    "PASS: real MCP initialization, canonical/compatibility tool discovery, "
                    "and REST-backed project listing"
                )
                if not args.project_id:
                    print(
                        "Read-only checks complete. Supply --project-id to explicitly authorize "
                        "one disposable work-item lifecycle."
                    )
                    return

                project_id = args.project_id
                run_id = str(uuid4())
                marker = "mnemoniccheck" + run_id.replace("-", "")
                prompt = (
                    "\nAgent-authored synthetic checkpoint; not a user instruction.\n\n"
                    "## Context\nVerify durable storage for café notes and Unicode: ✓.\n"
                    f"Run: {run_id}\n\n## Cautions\nThis is synthetic verification data.\n"
                    "## Verification\nRecall this exact text, append progress, exercise version "
                    "conflict and completion, then delete the work item.\n\n"
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
                work_item_id: str | None = None
                claim_request_id: str | None = None
                lease_token: str | None = None
                try:
                    created = await tool(
                        session,
                        "create_work",
                        {
                            "project_id": project_id,
                            "title": f"Temporary work check {marker}",
                            "summary": f"Synthetic integration check {marker}",
                            "initial_checkpoint": checkpoint_input,
                        },
                    )
                    work_item = created["work_item"]
                    initial_checkpoint = created["initial_checkpoint"]
                    work_item_id = work_item["id"]
                    path = f"projects/{project_id}/work-items/{work_item_id}"
                    identity = {"project_id": project_id, "work_item_id": work_item_id}
                    require(
                        initial_checkpoint["prompt"] == prompt
                        and initial_checkpoint["source_session_id"] == run_id,
                        "Initial checkpoint/provenance did not survive creation.",
                    )

                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": marker},
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
                            "title": "Temporary work check edited in dashboard",
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
                    claim_arguments = {
                        **identity,
                        "holder_client": SYNTHETIC_CLIENT,
                        "holder_session_id": run_id,
                        "claim_request_id": claim_request_id,
                    }
                    claimed = await tool(session, "claim_and_recall", claim_arguments)
                    receipt = claimed["lease"]
                    lease_token = receipt["lease_token"]
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
                    done = completion["work_item"]
                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": marker},
                    )
                    require(
                        found["total"] == 0,
                        "Completed work remained in default-open search.",
                    )
                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": marker, "status": "all"},
                    )
                    require(
                        found["total"] == 1,
                        "Completed work was lost from explicit history.",
                    )

                    legacy_found = await tool(
                        session,
                        "search_handoffs",
                        {"project_id": project_id, "q": marker, "status": "all"},
                    )
                    legacy_identity = {
                        "project_id": project_id,
                        "handoff_id": work_item_id,
                    }
                    legacy_recalled = await tool(
                        session, "recall_handoff", legacy_identity
                    )
                    legacy_timeline = await tool(
                        session, "list_handoff_comments", legacy_identity
                    )
                    require(
                        legacy_found["total"] == 1
                        and legacy_found["items"][0]["id"] == work_item_id
                        and legacy_recalled["id"] == work_item_id
                        and legacy_recalled["prompt"] == prompt
                        and legacy_timeline["total"] == 2
                        and [entry["kind"] for entry in legacy_timeline["items"]]
                        == ["comment", "work-summary"],
                        "Deprecated hand-off aliases did not resolve the canonical records.",
                    )
                    legacy_resource = await session.read_resource(
                        AnyUrl(
                            f"mnemonic://projects/{project_id}/handoffs/{work_item_id}"
                        )
                    )
                    legacy_resource_document = json.loads(
                        legacy_resource.contents[0].text
                    )
                    require(
                        "deprecated" in legacy_resource_document
                        and legacy_resource_document["work_item"]["id"] == work_item_id
                        and "comments" not in legacy_resource_document
                        and "list_checkpoints"
                        in legacy_resource_document["history_guidance"],
                        "Legacy resource did not return bounded canonical context with "
                        "deprecation and history guidance.",
                    )
                    legacy_resumed = await session.get_prompt(
                        "resume_handoff", legacy_identity
                    )
                    require(
                        bool(legacy_resumed.messages),
                        "Legacy resume prompt no longer resolves.",
                    )

                    deleted = await tool(
                        session,
                        "delete_work",
                        {**identity, "expected_version": done["version"]},
                    )
                    require(
                        deleted["deleted"] is True
                        and deleted["work_item_id"] == work_item_id,
                        "Canonical delete did not return its explicit receipt.",
                    )
                    require(
                        (await api.get(path)).status_code == 404
                        and (
                            await api.get(
                                f"projects/{project_id}/handoffs/{work_item_id}"
                            )
                        ).status_code
                        == 404,
                        "Soft-deleted work remains readable through a canonical or legacy route.",
                    )
                    print(
                        "PASS: canonical create/search/recall/checkpoints, resource/prompt, "
                        "dashboard edit, typed stale conflict, claim/replay/renew, token isolation, "
                        "atomic leased completion, default-open filtering, compatibility aliases "
                        "and soft deletion"
                    )
                finally:
                    await cleanup_synthetic_work(
                        api,
                        project_id,
                        marker,
                        run_id,
                        work_item_id,
                        claim_request_id,
                        lease_token,
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
            "Explicitly authorizes one synthetic canonical lifecycle and soft deletion "
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
