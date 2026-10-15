"""The FastAPI browser gate must retain the established Next request contract.

The fixture records JSON-representable calls from fourteen existing frontend
policy suites under Node 24. Large boundary values and non-finite JS numbers are
tested below instead of duplicating large strings in the fixture. The remaining
tests exercise Python-specific parser and header-container differences.
"""

import copy
import json
import re
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.routing import APIRoute
from starlette.datastructures import Headers
from starlette.routing import Match

from mnemonic_api.application import browser_policy as policy
from mnemonic_api.application import routes
from mnemonic_api.application.browser_policy.contracts import job_report
from mnemonic_api.application.browser_policy.values import integer, metadata, text
from mnemonic_api.application.browser_routes import browser_router

DOCUMENT = json.loads(
    (Path(__file__).parent / "fixtures" / "browser_policy_parity.json").read_text()
)
FIXTURES = DOCUMENT["cases"]
FUNCTIONS = {
    "allowedQueryKeys": policy.allowed_query_keys,
    "validQueryValues": policy.valid_query_values,
    "invalidMutationBody": policy.invalid_mutation_body,
    "forbiddenControlTransport": policy.forbidden_control_transport,
    "trustedRequest": policy.trusted_request,
    "proxyBodyLimitBytes": policy.proxy_body_limit_bytes,
}
PROJECT = "e36a7e53-938f-4c8a-b75a-af9c7331711a"
WORK = "7a5dc555-0a6d-4f92-9678-1647524827c8"
OPERATION = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55"
BASE = f"projects/{PROJECT}/work-items/{WORK}"
ACTOR = {"actor_client": "dashboard", "actor_session_id": "tab-1"}
CHECKPOINT = {"prompt": "A checkpoint", "source_client": "dashboard", "source_session_id": "tab-1"}
ORIGINS = frozenset({"http://localhost:3000", "http://127.0.0.1:3000"})


@pytest.mark.parametrize("case", FIXTURES, ids=lambda case: case["name"])
def test_existing_frontend_contract_fixtures(case):
    assert FUNCTIONS[case["name"]](*case["args"]) == case["result"]


@pytest.fixture(scope="module")
def registered_browser_router():
    return browser_router()


@pytest.mark.parametrize("case", DOCUMENT["route_inventory"])
def test_complete_method_query_inventory_matches_node_and_actual_routes(
    case, registered_browser_router
):
    path, method = case["args"]
    assert policy.allowed_query_keys(path, method) == case["result"]
    scope = {"type": "http", "method": method, "path": "/api/mnemonic/" + path, "root_path": ""}
    registered = any(
        route.matches(scope)[0] is Match.FULL for route in registered_browser_router.routes
    )
    assert registered is (case["result"] is not None)


def test_inventory_covers_every_current_machine_template_and_method():
    paths = set()
    for module in vars(routes).values():
        router = getattr(module, "router", None)
        if not isinstance(router, APIRouter):
            continue
        for route in router.routes:
            if isinstance(route, APIRoute):
                path = re.sub(
                    r"\{[^}]+\}",
                    lambda match: "recall-pointer" if match.group() == "{prompt_id}" else PROJECT,
                    route.path,
                )
                paths.add(path.lstrip("/"))
    expected = {
        (path, method)
        for path in paths
        for method in ("GET", "POST", "PATCH", "PUT", "DELETE", "HEAD", "OPTIONS")
    }
    assert {tuple(case["args"]) for case in DOCUMENT["route_inventory"]} == expected


@pytest.mark.parametrize("value", [True, False, "1", None, {}, [], -1, 1.5, 2**53, 10**1000])
def test_integer_controls_do_not_coerce_or_overflow(value):
    assert not integer(value)
    body = {"expected_version": value, "actor": ACTOR, "client_operation_id": OPERATION}
    assert policy.invalid_mutation_body(BASE + "/defer", "POST", body) is not None


@pytest.mark.parametrize("value", [1, 1.0, 2**53 - 1])
def test_browser_safe_integer_contract_accepts_integral_json_numbers(value):
    assert integer(value, 1)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_metadata_is_rejected(value):
    body = {
        "event_type": "progress",
        "body": "Progress",
        "metadata": {"number": value},
        "actor": ACTOR,
        "client_operation_id": OPERATION,
    }
    assert policy.invalid_mutation_body(BASE + "/events", "POST", body) == (
        policy.DEFINITIVE_PROXY_ERRORS["invalidProgressEvent"].detail
    )


def test_metadata_utf8_bound_counts_json_separators_and_nested_reserved_names():
    assert metadata({"a": "x" * 16_375})
    assert not metadata({"a": "x" * 16_376})
    assert not metadata({"a": [{"AuThOrIzAtIoN": "not a credential"}]})
    assert not metadata({"a": "\ud800"})
    assert not metadata({"a": "nul\0"})
    assert not metadata({"a": "😀" * 4_096})
    assert not metadata({"a": 10**1000})


@pytest.mark.parametrize(
    "number,wire_size",
    [
        (1.0, 1),
        (-0.0, 1),
        (1e-6, 8),
        (1e-7, 4),
        (1e20, 21),
        (1e21, 5),
    ],
)
def test_metadata_byte_limit_uses_browser_number_serialization(number, wire_size):
    # JSON.stringify(number), plus the same separator charge as the TS policy.
    padding = "x" * (16_384 - 27 - wire_size)
    assert metadata({"number": number, "padding": padding})
    assert not metadata({"number": number, "padding": padding + "x"})


def test_unicode_text_uses_javascript_trim_and_scalar_length():
    assert text("😀", 1)
    assert not text("😀😀", 1)
    assert not text("\ufeff", 1)
    assert text("\u0085", 1)
    assert text("\u001c", 1)
    assert not text("\ud800", 1)


@pytest.mark.parametrize("field", [b"host", b"origin", b"sec-fetch-site"])
def test_duplicate_trust_headers_cannot_select_a_favorable_value(field):
    ordinary = {
        b"host": b"localhost:3000",
        b"origin": b"http://localhost:3000",
        b"sec-fetch-site": b"same-origin",
    }
    headers = Headers(raw=[*ordinary.items(), (field, ordinary[field])])
    assert not policy.trusted_request(headers, "POST", ORIGINS)


def test_duplicate_cookie_headers_do_not_hide_a_control_cookie():
    headers = Headers(raw=[(b"cookie", b"lease_token=not-a-token"), (b"cookie", b"theme=dark")])
    assert policy.forbidden_control_transport(headers)


def test_origin_is_not_bearer_authentication_or_forwarded_header_trust():
    headers = {
        "host": "attacker.test",
        "origin": "http://localhost:3000",
        "x-forwarded-host": "localhost:3000",
        "x-forwarded-proto": "http",
        "authorization": "Bearer a-fake-machine-credential",
    }
    assert not policy.trusted_request(headers, "POST", ORIGINS)
    assert policy.trusted_request({"host": "localhost:3000"}, "GET", ORIGINS)
    assert not policy.trusted_request({"host": "localhost:3000"}, "POST", ORIGINS)


@pytest.mark.parametrize("capability", ["lease_token", "gate_id", "Gate_Type"])
def test_browser_capabilities_are_rejected_even_on_otherwise_unvalidated_project_creation(
    capability,
):
    body = {"name": "New project", "metadata": [{capability: "redacted"}]}
    assert policy.invalid_mutation_body("projects", "POST", body) == (
        f"The request body contains an unsupported field: {capability}."
    )


@pytest.mark.parametrize("suffix", ["claim", "claim-and-recall", "renew-claim", "release-claim"])
def test_no_lease_or_agent_discovery_route_is_added(suffix):
    assert policy.allowed_query_keys(BASE + "/" + suffix, "POST") is None
    assert policy.allowed_query_keys(f"projects/{PROJECT}/ready-work", "GET") is None


def test_completion_keeps_historical_sparse_replay_but_rejects_agent_paths():
    body = {"expected_version": 1, "checkpoint": CHECKPOINT, "client_operation_id": OPERATION}
    assert policy.invalid_mutation_body(BASE + "/complete", "POST", body) is None
    assert (
        policy.invalid_mutation_body(
            BASE + "/complete", "POST", {**body, "subagent_transcripts": None}
        )
        is None
    )
    assert (
        policy.invalid_mutation_body(
            BASE + "/complete",
            "POST",
            {**body, "subagent_transcripts": [{"path": "/tmp/agent.jsonl"}]},
        )
        is not None
    )
    assert (
        policy.invalid_mutation_body(
            BASE + "/complete", "POST", {**body, "job_completion_report": None}
        )
        is not None
    )


def test_review_request_provenance_and_nested_handoff_remain_bounded():
    body = {
        "expected_version": 1,
        "actor": ACTOR,
        "client_operation_id": OPERATION,
        "request_code_review": True,
        "request_code_review_mode": "warm",
    }
    assert policy.invalid_mutation_body(BASE, "PATCH", body) is None
    for actor in (
        {"actor_client": "codex", "actor_session_id": "thread"},
        {**ACTOR, "actor_model": "model"},
    ):
        assert policy.invalid_mutation_body(BASE, "PATCH", {**body, "actor": actor}) is not None
    assert (
        policy.invalid_mutation_body(BASE, "PATCH", {**body, "request_code_review_mode": "cold"})
        is not None
    )
    assert (
        policy.invalid_mutation_body(BASE, "PATCH", {**body, "title": "A combined unrelated edit"})
        is not None
    )


@pytest.mark.parametrize(
    "url,accepted",
    [
        ("https:example.com", True),
        ("https:///example.com", True),
        ("https://@example.com", True),
        ("https://a_b", True),
        ("https://%ZZ", False),
        ("https://999.1", False),
        ("https://foo|bar", False),
        ("https://user@example.com", False),
        ("https://example.com/has space", False),
    ],
)
def test_project_url_validation_matches_browser_url_parsing(url, accepted):
    result = policy.invalid_mutation_body(f"projects/{PROJECT}", "PATCH", {"repository_url": url})
    assert (result is None) is accepted


def test_review_handoff_keeps_raw_identity_and_browser_url_parsing():
    handoff = {
        "scope": {
            "repositories": [
                {
                    "repository_key": "main",
                    "repository_url": "https:example.com",
                    "object_format": "sha1",
                    "base_commit": "a" * 40,
                    "head_commit": "b" * 40,
                }
            ]
        },
        "handoff": {
            "change_summary": "Changed",
            "validation_summary": "Checked",
            "decisions": [],
            "focus_areas": [],
            "traps": [],
        },
    }
    body = {
        "expected_version": 1,
        "actor": ACTOR,
        "client_operation_id": OPERATION,
        "request_code_review": True,
        "request_code_review_mode": "cold",
        "code_review_handoff": handoff,
    }
    before = copy.deepcopy(body)
    assert policy.invalid_mutation_body(BASE, "PATCH", body) is None
    assert body == before
    handoff["scope"]["repositories"][0]["repository_url"] = "https://%ZZ"
    assert policy.invalid_mutation_body(BASE, "PATCH", body) is not None


def test_report_revision_and_prose_have_exact_wire_bounds():
    report = {"summary": "Summary", "fyi_items": [], "prompt_revision": "9223372036854775807"}
    assert job_report(report)
    for revision in (1, "01", "0", "9223372036854775808"):
        assert not job_report({**report, "prompt_revision": revision})
    assert not job_report({**report, "summary": "\ufeff"})
    assert not job_report({**report, "summary": "Line one\nline two"})
    assert not job_report({**report, "fyi_items": ["😀" * 600] * 10})


def test_unified_search_semantic_and_content_filters_require_their_exact_opt_ins():
    path = f"projects/{PROJECT}/search"
    body = {
        "q": "two terms",
        "facets": ["artifacts"],
        "fulltext": True,
        "filters": {"artifacts": {"semantic": True}},
    }
    assert policy.invalid_mutation_body(path, "POST", body) is None
    for changes in (
        {"q": '"phrase"'},
        {"q": " "},
        {"fulltext": False},
        {"query_mode": "phrase"},
        {"facets": ["work_items"]},
    ):
        assert policy.invalid_mutation_body(path, "POST", {**body, **changes}) is not None
    transcript = {"filters": {"transcripts": {"content_kinds": ["reasoning"]}}}
    assert policy.invalid_mutation_body(path, "POST", transcript) is not None
    assert policy.invalid_mutation_body(path, "POST", {**transcript, "fulltext": True}) is None


def test_search_and_suggestion_caps_do_not_expand_other_routes():
    assert policy.proxy_body_limit_bytes(f"projects/{PROJECT}/search") == 16_384
    assert policy.proxy_body_limit_bytes(f"projects/{PROJECT}/duplicate-suggestions") == 2_097_152
    assert policy.proxy_body_limit_bytes(BASE + "/complete") == 1_048_576
    assert (
        policy.proxy_body_limit_bytes(f"projects/{PROJECT}/duplicate-suggestions/extra")
        == 1_048_576
    )


def test_every_fixture_is_stable_across_repeated_validation():
    # Validators must never normalize or rewrite an operation's retry payload.
    for case in FIXTURES:
        before = copy.deepcopy(case["args"])
        FUNCTIONS[case["name"]](*case["args"])
        assert case["args"] == before
