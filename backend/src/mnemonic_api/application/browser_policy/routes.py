"""The main dashboard JSON allowlist, deliberately smaller than the REST API."""

import re

UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
PROJECT = rf"projects/{UUID}"
WORK = rf"{PROJECT}/work-items/{UUID}"
DECISION = rf"{PROJECT}/knowledge/decisions/[A-Z]{{1,5}}-[0-9]{{5,19}}"
PROMPT_IDS = (
    "recall-pointer",
    "job-completion-report",
    "cold-code-review",
    "warm-code-review",
    "review-remediation",
    "resume-work",
    "review-recommendation",
    "request-human-review",
    "decision-impact-review",
)
PROMPT_DETAIL = re.compile(rf"{PROJECT}/prompts/({'|'.join(PROMPT_IDS)})")

# Each entry names one browser contract and its exact method/query surface.
_ROUTES = (
    ("knowledge_commits", rf"{PROJECT}/knowledge/commits",
     {"GET": "repository commit_hash limit offset"}),
    ("knowledge_domains", rf"{PROJECT}/knowledge/domains", {"GET": "limit offset", "POST": ""}),
    ("knowledge_domain", rf"{PROJECT}/knowledge/domains/{UUID}", {"PATCH": ""}),
    ("knowledge_decisions", rf"{PROJECT}/knowledge/decisions",
     {"GET": "domain_id state q limit offset"}),
    ("knowledge_decision", rf"{DECISION}(?:-[0-9]{{1,10}})?",
     {"GET": "revision line_start line_end"}),
    ("knowledge_history", rf"{DECISION}/history",
     {"GET": "limit offset"}),
    ("knowledge_scope", rf"{DECISION}/scope",
     {"GET": "revision limit offset"}),
    ("knowledge_impact", rf"{DECISION}(?:-[0-9]{{1,10}})?/impact",
     {"GET": "revision max_depth limit offset"}),
    ("knowledge_assessments", rf"{PROJECT}/knowledge/assessments",
     {"GET": "decision_id limit offset"}),
    ("knowledge_assessment", rf"{PROJECT}/knowledge/assessments/{UUID}",
     {"GET": "limit offset"}),
    ("knowledge_dispositions", rf"{PROJECT}/knowledge/assessments/{UUID}/dispositions",
     {"GET": "limit offset", "POST": ""}),
    ("projects", "projects", {"GET": "limit offset", "POST": ""}),
    ("project", PROJECT, {"GET": "", "PATCH": ""}),
    ("settings", rf"{PROJECT}/settings", {"GET": "work_item_id", "PATCH": ""}),
    ("rubric", rf"{PROJECT}/priority-rubric", {"GET": "", "PATCH": ""}),
    ("prompts", rf"{PROJECT}/prompts", {"GET": ""}),
    ("prompt", PROMPT_DETAIL.pattern, {"GET": "", "PUT": ""}),
    ("prompt_render", PROMPT_DETAIL.pattern + "/render", {"POST": ""}),
    ("search", rf"{PROJECT}/search", {"POST": ""}),
    ("suggestions", rf"{PROJECT}/duplicate-suggestions", {"POST": ""}),
    ("tasks", rf"{PROJECT}/tasks", {"GET": "kind status task_id limit offset"}),
    (
        "reviews",
        rf"{PROJECT}/(?:code-reviews|work-agent-follow-ups)",
        {"GET": "state availability work_item_id after limit"},
    ),
    ("review", rf"{WORK}/code-reviews/{UUID}", {"GET": ""}),
    ("follow_up", rf"{WORK}/agent-follow-ups/{UUID}", {"GET": ""}),
    ("follow_up_answer", rf"{WORK}/agent-follow-ups/{UUID}/answer", {"POST": ""}),
    ("activity", rf"{PROJECT}/activity", {"GET": "after start limit"}),
    ("report_count", rf"{PROJECT}/job-completion-reports/count", {"GET": ""}),
    (
        "reports",
        rf"{PROJECT}/job-completion-reports",
        {"GET": "dismissal work_item_id limit cursor"},
    ),
    ("report", rf"{PROJECT}/job-completion-reports/{UUID}", {"GET": ""}),
    ("report_dismiss", rf"{PROJECT}/job-completion-reports/{UUID}/dismiss", {"POST": ""}),
    (
        "report_follow_ups",
        rf"{PROJECT}/job-completion-reports/{UUID}/follow-ups",
        {"GET": "limit cursor", "POST": ""},
    ),
    ("work_report_follow_ups", rf"{WORK}/report-follow-ups", {"GET": "direction limit cursor"}),
    (
        "work_items",
        rf"{PROJECT}/work-items",
        {
            "GET": "q semantic status status_scope sort tag source_client external_url "
            "source_session_id view detail duplicate_scope canonical_work_item_id "
            "created_after created_before updated_after updated_before diagnostics "
            "query_mode work_fields limit offset pinned_work_item_ids",
            "POST": "",
        },
    ),
    ("work_item", WORK, {"GET": "", "PATCH": ""}),
    ("checkpoints", rf"{WORK}/checkpoints", {"GET": "order limit offset", "POST": ""}),
    ("context", rf"{WORK}/context", {"GET": "recent_limit recent_event_limit"}),
    (
        "children",
        rf"{WORK}/children",
        {
            "GET": "status status_scope sort tag source_client source_session_id "
            "limit offset pinned_work_item_ids",
        },
    ),
    ("work_relationships", rf"{WORK}/relationships", {"GET": "direction type limit offset"}),
    ("relationships", rf"{PROJECT}/relationships", {"POST": ""}),
    ("relationship", rf"{PROJECT}/relationships/{UUID}", {"DELETE": ""}),
    ("events", rf"{WORK}/events", {"GET": "order event_type limit offset", "POST": ""}),
    ("evidence", rf"{WORK}/completion-evidence", {"GET": "limit cursor"}),
    ("attention", rf"{PROJECT}/human-attention", {"GET": "work_item_id limit cursor"}),
    ("gates", rf"{WORK}/gates", {"GET": "status limit cursor"}),
    ("gate_context", rf"{WORK}/gates/{UUID}/context", {"GET": "recent_limit recent_event_limit"}),
    ("gate_resolve", rf"{WORK}/gates/{UUID}/resolve", {"POST": ""}),
    ("complete", rf"{WORK}/complete", {"POST": ""}),
    ("defer", rf"{WORK}/defer", {"POST": ""}),
    ("pending", rf"{WORK}/return-to-pending", {"POST": ""}),
    ("delete", rf"{WORK}/delete", {"POST": ""}),
    ("move", rf"{WORK}/move", {"POST": ""}),
    ("merge", rf"{WORK}/merge", {"POST": ""}),
)
ROUTES = tuple((name, re.compile(pattern), methods) for name, pattern, methods in _ROUTES)
PROTECTED_MUTATIONS = frozenset(
    {
        ("knowledge_domains", "POST"),
        ("knowledge_domain", "PATCH"),
        ("knowledge_dispositions", "POST"),
        ("follow_up_answer", "POST"),
        ("work_items", "POST"),
        ("checkpoints", "POST"),
        ("events", "POST"),
        ("relationships", "POST"),
        ("work_item", "PATCH"),
        ("complete", "POST"),
        ("defer", "POST"),
        ("delete", "POST"),
        ("move", "POST"),
        ("merge", "POST"),
        ("relationship", "DELETE"),
        ("gate_resolve", "POST"),
        ("report_dismiss", "POST"),
        ("report_follow_ups", "POST"),
    }
)


def route_name(path: str, method: str) -> str | None:
    return next(
        (
            name
            for name, pattern, methods in ROUTES
            if method in methods and pattern.fullmatch(path)
        ),
        None,
    )


def allowed_query_keys(path: str, method: str) -> list[str] | None:
    return next(
        (
            methods[method].split()
            for _, pattern, methods in ROUTES
            if method in methods and pattern.fullmatch(path)
        ),
        None,
    )


def valid_query_values(key: str, values: list[str]) -> bool:
    if key == "pinned_work_item_ids":
        return 0 < len(values) <= 100 and all(re.fullmatch(UUID, value) for value in values)
    return len(values) == 1


def proxy_body_limit_bytes(path: str) -> int:
    name = route_name(path, "POST")
    if name == "search":
        return 16_384
    return 2_097_152 if name == "suggestions" else 1_048_576
