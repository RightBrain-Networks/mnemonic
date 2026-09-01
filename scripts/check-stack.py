"""Exercise the live HTTP MCP -> REST -> PostgreSQL path and dashboard proxy.

Run with the MCP project's Python environment. Checks are read-only unless a
project is explicitly authorized with --project-id. The write check creates a
small, uniquely marked Phase 6 work graph, exercises ready discovery and its
canonical event lifecycle, then removes the graph and soft-deletes every
synthetic item it created. Immutable events remain attached to those hidden
items. Never authorize writes against a project without permission.
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
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

CANONICAL_TOOLS = {
    "list_projects",
    "create_project",
    "create_work",
    "search_work",
    "list_ready_work",
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
    "append_event",
    "list_work_events",
}
PROTECTED_MUTATION_TOOLS = {
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
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
    return f"Synthetic Phase 6 integration check {marker}"


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
        json.dumps(arguments, sort_keys=True, separators=(",", ":"))
        == encoded_arguments,
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
    return (
        isinstance(arguments, dict)
        and arguments.get("client_operation_id") == operation_id
    )


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
        should_drop = (
            not self.dropped
            and _is_target_tool_call(payload, self.name, self.operation_id)
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

    def client_factory(**options: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=options.get("headers"),
            timeout=options.get("timeout"),
            auth=options.get("auth"),
            transport=transport,
            follow_redirects=True,
        )

    caller_observed_failure = False
    try:
        async with streamablehttp_client(
            mcp_url,
            headers=headers,
            timeout=15,
            httpx_client_factory=client_factory,
        ) as (read, write, _), ClientSession(read, write) as loss_session:
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


async def work_events(
    session: ClientSession, identity: dict[str, str]
) -> list[dict[str, Any]]:
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
        len(matches) <= 5,
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
    """Safety-clean this run's graph with exact retained cleanup retries."""
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
        remove_arguments = retained_mutation(
            {"actor": mutation_actor(run_id)}
        )
        removed = await retained_api_mutation(
            api,
            "DELETE",
            relationship_path,
            remove_arguments,
        )
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
            and record["status"] == "pending"
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
        if cleanup_token is not None and record["status"] == "pending":
            release_arguments = retained_mutation(
                {
                    "lease_token": cleanup_token,
                    "actor": mutation_actor(run_id),
                }
            )
            released = await retained_api_mutation(
                api,
                "POST",
                path + "/release-claim",
                release_arguments,
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
        delete_arguments = retained_mutation(
            {
                "expected_version": record["version"],
                "actor": mutation_actor(run_id),
            }
        )
        cleanup = await retained_api_mutation(
            api,
            "POST",
            path + "/delete",
            delete_arguments,
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
                tools_by_name = {entry.name: entry for entry in catalog.tools}
                require(
                    len(catalog.tools) == 22
                    and len(tools_by_name) == 22
                    and len(PROTECTED_MUTATION_TOOLS) == 9
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
                            and properties.get("client_operation_id", {}).get("format")
                            == "uuid",
                            f"MCP {name} does not require a UUID client operation ID.",
                        )
                    else:
                        require(
                            "client_operation_id" not in properties
                            and "client_operation_id" not in required,
                            f"MCP {name} unexpectedly exposes a client operation ID.",
                        )
                await tool(session, "list_projects", {})
                print(
                    "PASS: real MCP initialization, 22-tool catalog, exact nine protected "
                    "mutation schemas/annotations, and REST-backed project listing"
                )
                if not args.project_id:
                    print(
                        "Read-only checks complete. Supply --project-id to explicitly authorize "
                        "one disposable Phase 6 ready/event lifecycle."
                    )
                    return

                project_id = args.project_id
                if args.cleanup_run_id:
                    cleanup_run_id = args.cleanup_run_id
                    cleanup_marker = "mnemoniccheck" + cleanup_run_id.replace("-", "")
                    await cleanup_synthetic_work(
                        api,
                        project_id,
                        cleanup_marker,
                        cleanup_run_id,
                        set(),
                        set(),
                        {},
                        {},
                    )
                    require(
                        not await find_synthetic_work(
                            api, project_id, cleanup_marker, cleanup_run_id
                        ),
                        "Interrupted-run cleanup left readable synthetic work.",
                    )
                    print("PASS: interrupted synthetic run cleanup")
                    return
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
                        created["initial_relationships"] == [],
                        "Unlinked creation returned unexpected relationships.",
                    )
                    await require_event_types(session, identity, ["work_created"])

                    found = await tool(
                        session,
                        "search_work",
                        {"project_id": project_id, "q": primary_marker},
                    )
                    require(
                        found["total"] == 1
                        and found["items"][0]["work_item"]["id"] == work_item_id
                        and "current_context" not in found["items"][0]
                        and "summary" not in found["items"][0]["work_item"],
                        "Unique pointer-only work search failed.",
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
                    resource = await session.read_resource(
                        AnyUrl(
                            f"mnemonic://projects/{project_id}/work-items/{work_item_id}"
                        )
                    )
                    resource_context = json.loads(resource.contents[0].text)
                    require(
                        resource_context["initial_checkpoint"]["prompt"] == prompt
                        and resource_context["current_context"] is None
                        and resource_context["current_context_is_initial"] is True,
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
                        and typed_error_code(bearer_echo)
                        == "client_operation_secret_echo"
                        and key not in bearer_echo.text,
                        "Protected progress did not reject the request bearer value-free.",
                    )
                    require(
                        [
                            event["event_type"]
                            for event in await work_events(session, identity)
                        ]
                        == ["work_created"],
                        "Rejected bearer echo changed work history.",
                    )

                    progress_body = (
                        "Synthetic Phase 6 progress preserved exact Unicode: café ✓."
                    )
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
                    await require_event_types(
                        session, identity, ["work_created", "progress"]
                    )

                    progress_prompt = (
                        "Live validation preserved exact context and reached the mutation "
                        "checks."
                    )
                    progress_input = {
                        "prompt": progress_prompt,
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": None,
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
                    await require_event_types(
                        session,
                        identity,
                        ["work_created", "progress", "checkpoint_added"],
                    )
                    unchanged = await tool(session, "get_work", identity)
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
                    require(
                        edit.status_code == 200, "Dashboard proxy work edit failed."
                    )
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
                        and claimed["context"]["recent_events"][-1]["event_type"]
                        == "work_claimed"
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
                        and typed_error_code(token_echo)
                        == "client_operation_secret_echo"
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
                        denied_claim.status_code == 404
                        and denied_token.status_code == 400,
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

                    ready_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary ready work check {ready_marker}",
                            "summary": synthetic_summary(marker),
                            "priority": 50,
                            "initial_checkpoint": {
                                **checkpoint_input,
                                "prompt": f"Synthetic ready candidate for run {run_id}.",
                                "tags": [run_tag, "verification", "ready"],
                            },
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
                    ready_identity = {
                        "project_id": project_id,
                        "work_item_id": ready_id,
                    }
                    await require_event_types(session, ready_identity, ["work_created"])

                    terminal_create_arguments = retained_mutation(
                        {
                            "project_id": project_id,
                            "title": f"Temporary terminal work check {terminal_marker}",
                            "summary": synthetic_summary(marker),
                            "priority": 30,
                            "status": "wont-do",
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
                    await require_event_types(
                        session, terminal_identity, ["work_created"]
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
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
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_page["total"] == 1
                        and ready_ids(ready_page) == [ready_id],
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
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
                        and [
                            event["id"]
                            for event in await work_events(session, identity)
                        ]
                        == [event["id"] for event in events_after_unblock],
                        "New-key absent removal did not bind and replay removed=false.",
                    )
                    unblocked_context = await tool(session, "recall_work", identity)
                    require(
                        unblocked_context["readiness"]["is_ready"] is True
                        and unblocked_context["readiness"]["is_blocked"] is False
                        and unblocked_context["readiness"]["unresolved_blocker_count"]
                        == 0,
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
                        session, terminal_identity, ["work_created", "work_reopened"]
                    )
                    ready_page = await tool(
                        session,
                        "list_ready_work",
                        {"project_id": project_id, "tag": run_tag, "limit": 100},
                    )
                    require(
                        ready_ids(ready_page)
                        == [work_item_id, blocker_id, ready_id, terminal_id],
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
                        and children.json()["items"][0]["summary"]["work_item"]["id"]
                        == child_id,
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
                            "Synthetic validation completed: exact creation, pointer search, "
                            "bounded recall, checkpoint history, dashboard edit, and conflict "
                            "detection were observed working."
                        ),
                        "source_client": SYNTHETIC_CLIENT,
                        "source_session_id": run_id,
                        "source_model": None,
                        "source_session_url": None,
                        "repository_branch": None,
                        "verified_against": None,
                        "tags": [run_tag, "verification", "complete"],
                        "source_metadata": {"synthetic_check": True},
                    }
                    completion_arguments = retained_mutation(
                        {
                            **identity,
                            "expected_version": current["version"],
                            "checkpoint": completion_input,
                            "lease_token": lease_token,
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
                        and completion["checkpoint"]["prompt"]
                        == completion_input["prompt"],
                        "Completion did not atomically save its checkpoint and done status.",
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
                            "relationship_added",
                            "relationship_added",
                            "work_completed",
                        ],
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
                    final_events = await require_event_types(
                        session, identity, [*timeline_before_reopen, "work_reopened"]
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
                            event["event_type"] == "progress"
                            and event["body"] == progress_body
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
                        == [work_item_id, blocker_id, ready_id, terminal_id, child_id],
                        "Final ready results did not follow priority-first order.",
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
                        latest = await tool(session, "get_work", item_identity)
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
                            deleted["deleted"] is True
                            and deleted["work_item_id"] == item_id,
                            "Canonical delete did not return its explicit receipt.",
                        )

                    for item_id in synthetic_ids:
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
                        "PASS: canonical create/search/recall/checkpoints/events, resource/prompt, "
                        "dashboard edit, typed stale conflict, claim/replay/renew/release, "
                        "pointer and capability isolation, exact ready discovery and reappearance, "
                        "event replay/no-op behavior, atomic child/discovery, hierarchy browse, "
                        "leased completion/reopen, graph removal and soft deletion"
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
            "Explicitly authorizes one synthetic Phase 6 ready/event lifecycle and cleanup "
            "inside this project"
        ),
    )
    parser.add_argument(
        "--other-project-id",
        type=project_uuid,
        help="Optional second project for an isolation check",
    )
    parser.add_argument(
        "--cleanup-run-id",
        type=project_uuid,
        help=(
            "Clean only exact synthetic marker data from an interrupted run; retry after "
            "lease expiry if its capability token was lost"
        ),
    )
    args = parser.parse_args()
    if args.other_project_id:
        if not args.project_id:
            parser.error("--other-project-id requires --project-id.")
        if args.other_project_id == args.project_id:
            parser.error("--other-project-id must identify a different project.")
    if args.cleanup_run_id and not args.project_id:
        parser.error("--cleanup-run-id requires --project-id.")
    if args.cleanup_run_id and args.other_project_id:
        parser.error("--cleanup-run-id cannot be combined with --other-project-id.")
    key = values.get("MNEMONIC_API_KEY", "")
    if len(key) < 32:
        parser.error("Set MNEMONIC_API_KEY in .env or the environment first.")
    asyncio.run(check(args, key))


if __name__ == "__main__":
    main()
