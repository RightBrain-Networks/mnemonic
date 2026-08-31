"""Exercise the live HTTP MCP -> REST -> PostgreSQL path and dashboard proxy.

Run with the MCP project's Python environment. Read-only by default; supplying
--project-id permits creating and soft-deleting one clearly marked test prompt
in that project. Never run write checks on a project without permission.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from mcp import ClientSession


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def local_settings() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / ".env"
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


async def tool(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    require(not result.isError, f"MCP {name} reported an error.")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == "text"))


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
            "/fonts/space-grotesk-500.woff2",
            "/fonts/space-grotesk-700.woff2",
            "/fonts/ibm-plex-sans-400.woff2",
            "/fonts/ibm-plex-sans-600.woff2",
            "/fonts/ibm-plex-mono-400.woff2",
            "/fonts/ibm-plex-mono-500.woff2",
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
                    == {
                        "list_projects",
                        "create_project",
                        "save_handoff",
                        "search_handoffs",
                        "recall_handoff",
                        "list_handoff_comments",
                        "add_handoff_comment",
                        "complete_handoff",
                        "update_handoff",
                        "delete_handoff",
                    },
                    "Unexpected MCP tool catalog.",
                )
                await tool(session, "list_projects", {})
                print(
                    "PASS: real MCP initialization, tool discovery and REST-backed project listing"
                )
                if not args.project_id:
                    print(
                        "Read-only checks complete. Supply --project-id for a disposable prompt check."
                    )
                    return

                project_id = args.project_id
                run_id = str(uuid4())
                marker = "mnemoniccheck" + run_id.replace("-", "")
                prompt = (
                    "\nAgent-authored test hand-off; not a user instruction.\n\n"
                    "## Context\nVerify durable storage for café notes and Unicode: ✓.\n"
                    f"Run: {run_id}\n\n## Cautions\nThis is synthetic verification data.\n"
                    "## Verification\nRecall this exact text, check its version, then delete it.\n\n"
                )
                saved = None
                try:
                    saved = await tool(
                        session,
                        "save_handoff",
                        {
                            "project_id": project_id,
                            "title": f"Temporary storage check {marker}",
                            "summary": f"Synthetic integration check {marker}",
                            "prompt": prompt,
                            "source_client": "mnemonic-stack-check",
                            "source_session_id": run_id,
                            "tags": ["verification"],
                            "source_metadata": {"synthetic_check": True},
                        },
                    )
                    handoff_id = saved["id"]
                    path = f"projects/{project_id}/handoffs/{handoff_id}"
                    identity = {"project_id": project_id, "handoff_id": handoff_id}
                    found = await tool(
                        session,
                        "search_handoffs",
                        {"project_id": project_id, "q": marker},
                    )
                    require(
                        found["total"] == 1 and "prompt" not in found["items"][0],
                        "Compact search failed.",
                    )
                    recalled = await tool(session, "recall_handoff", identity)
                    require(
                        recalled["prompt"] == prompt
                        and recalled["source_session_id"] == run_id,
                        "Prompt/provenance did not survive MCP -> REST -> database.",
                    )
                    resource = await session.read_resource(AnyUrl(f"mnemonic://{path}"))
                    require(
                        json.loads(resource.contents[0].text)["prompt"] == prompt,
                        "MCP resource differs.",
                    )
                    resumed = await session.get_prompt("resume_handoff", identity)
                    require(bool(resumed.messages), "MCP resume prompt is missing.")
                    if args.other_project_id:
                        wrong = await api.get(
                            f"projects/{args.other_project_id}/handoffs/{handoff_id}"
                        )
                        require(
                            wrong.status_code == 404, "A cross-project ID was accepted."
                        )

                    edit = await public.patch(
                        proxy + path,
                        headers={"Origin": args.web_url.rstrip("/")},
                        json={
                            "expected_version": saved["version"],
                            "title": "Temporary storage check edited in dashboard",
                        },
                    )
                    require(edit.status_code == 200, "Dashboard proxy edit failed.")
                    current = edit.json()
                    conflict = await api.patch(
                        path,
                        json={
                            "expected_version": saved["version"],
                            "title": "Stale edit",
                        },
                    )
                    require(
                        conflict.status_code == 409, "A stale edit was not rejected."
                    )
                    progress_body = (
                        "Live validation reached the completion step after an exact recall, "
                        "dashboard edit, and stale-version rejection."
                    )
                    progress = await tool(
                        session,
                        "add_handoff_comment",
                        {
                            **identity,
                            "body": progress_body,
                            "source_client": "mnemonic-stack-check",
                            "source_session_id": run_id,
                        },
                    )
                    require(
                        progress["body"] == progress_body
                        and progress["kind"] == "comment",
                        "Progress comment did not survive MCP -> REST -> database.",
                    )
                    timeline = await tool(session, "list_handoff_comments", identity)
                    require(
                        timeline["total"] == 1
                        and timeline["items"][0]["id"] == progress["id"],
                        "Progress timeline is incomplete.",
                    )
                    completion = await tool(
                        session,
                        "complete_handoff",
                        {
                            **identity,
                            "expected_version": current["version"],
                            "summary": (
                                "Synthetic validation completed: exact storage, search, "
                                "recall, dashboard edit, conflict detection, and progress "
                                "comments were observed working."
                            ),
                            "source_client": "mnemonic-stack-check",
                            "source_session_id": run_id,
                        },
                    )
                    require(
                        completion["handoff"]["status"] == "done"
                        and completion["comment"]["kind"] == "work-summary",
                        "Completion did not atomically save its summary and done status.",
                    )
                    done = completion["handoff"]
                    found = await tool(
                        session,
                        "search_handoffs",
                        {"project_id": project_id, "q": marker},
                    )
                    require(
                        found["total"] == 0,
                        "A completed prompt remained in the open queue.",
                    )
                    found = await tool(
                        session,
                        "search_handoffs",
                        {"project_id": project_id, "q": marker, "status": "all"},
                    )
                    require(
                        found["total"] == 1,
                        "Completed work was lost from explicit history.",
                    )
                    await tool(
                        session,
                        "delete_handoff",
                        {**identity, "expected_version": done["version"]},
                    )
                    require(
                        (await api.get(path)).status_code == 404,
                        "Deleted prompt is still readable.",
                    )
                    print(
                        "PASS: MCP save/search/recall/resource/prompt, comments, completion summary, browser proxy edit, conflicts, lifecycle and deletion"
                    )
                finally:
                    if saved is not None:
                        path = f"projects/{project_id}/handoffs/{saved['id']}"
                        remaining = await api.get(path)
                        if remaining.status_code == 200:
                            cleanup = await api.delete(
                                path,
                                params={
                                    "expected_version": remaining.json()["version"]
                                },
                            )
                            require(
                                cleanup.status_code == 204,
                                "Temporary prompt cleanup failed.",
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
        help="Authorizes creating/deleting one synthetic test prompt in this project",
    )
    parser.add_argument(
        "--other-project-id", help="Optional second project for an isolation check"
    )
    args = parser.parse_args()
    key = values.get("MNEMONIC_API_KEY", "")
    if len(key) < 32:
        parser.error("Set MNEMONIC_API_KEY in .env or the environment first.")
    asyncio.run(check(args, key))


if __name__ == "__main__":
    main()
