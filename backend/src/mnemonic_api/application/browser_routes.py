"""Explicit browser registrations of existing domain handlers.

This is a second transport, not a second domain implementation. Only the named
handlers below are registered. Their validation, dependencies, response models,
receipts and request-local mutation traces are retained by FastAPI itself.
Machine authentication remains on the independent /api/v1 router.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.routing import APIRoute

from mnemonic_api.application import routes
from mnemonic_api.application.browser_runtime import runtime_config

BROWSER_PREFIX = "/api/mnemonic"
_ADMITTED = object()

# The names are a deliberately closed inventory: adding a machine endpoint never
# automatically exposes it to the dashboard. Policy also validates concrete IDs.
BROWSER_HANDLERS = {
    "knowledge": (
        "list_knowledge_domains", "create_knowledge_domain", "rename_knowledge_domain",
        "list_knowledge_decisions", "get_knowledge_decision", "get_knowledge_decision_history",
        "get_knowledge_decision_scope",
    ),
    "projects": (
        "list_projects", "create_project", "get_project", "update_project",
        "get_project_settings", "update_project_settings",
    ),
    "priority_rubrics": ("get_priority_rubric", "update_priority_rubric"),
    "prompts": ("list_prompts", "get_prompt", "update_prompt", "render_project_prompt"),
    "work_search": ("search_work",),
    "work_items": (
        "create_work", "get_work", "update_work", "defer_work", "move_work",
        "complete_work", "delete_work", "recall_work", "list_children",
    ),
    "leases": ("return_work_to_pending_from_dashboard",),
    "history": ("list_checkpoints", "add_checkpoint", "get_work_events", "append_event"),
    "relationships": ("add_relationship", "remove_relationship", "list_relationships"),
    "duplicates": ("duplicate_suggestions", "merge_work"),
    "search": ("search_project",),
    "tasks": ("list_tasks",),
    "completion_evidence": ("list_completion_evidence",),
    "human_gates": (
        "get_human_attention", "get_work_gates", "resolve_human_gate_route",
        "review_human_gate_context",
    ),
    "code_reviews": (
        "list_code_reviews", "list_work_follow_ups", "get_code_review",
        "get_work_follow_up", "respond_to_work_follow_up",
    ),
    "project_reports": (
        "get_activity", "list_reports", "count_reports", "get_report",
        "get_report_follow_ups", "get_work_report_follow_ups", "dismiss", "follow_up",
    ),
}


def require_browser_admission(request: Request) -> None:
    """Defense in depth: no route can run without the pre-body boundary."""
    if getattr(request.state, "browser_admission", None) is not _ADMITTED:
        raise HTTPException(403, "This dashboard request is not from a trusted origin.")


def browser_router() -> APIRouter:
    router = APIRouter(
        prefix=BROWSER_PREFIX,
        dependencies=[Depends(require_browser_admission)],
        include_in_schema=False,
    )
    router.add_api_route("/runtime-config", runtime_config, methods=["GET"])
    for module_name, names in BROWSER_HANDLERS.items():
        source = getattr(routes, module_name).router
        selected = [route for route in source.routes
                    if isinstance(route, APIRoute) and route.name in names]
        if {route.name for route in selected} != set(names):
            raise RuntimeError(f"Browser handler inventory changed: {module_name}")
        subset = APIRouter()
        subset.routes.extend(selected)
        router.include_router(subset)
    return router
