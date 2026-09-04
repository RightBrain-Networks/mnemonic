"""Executable OpenAPI 3.1 coverage for the Phase 11 JSON Schema contract."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from mnemonic_api.schemas import (
    COMPLETION_EVENT_ID_MAX,
    COMPLETION_EXPECTED_VERSION_MAX,
    CompletionEvidenceInput,
    CompletionEvidenceListQuery,
    CompletionEvidencePage,
    WorkCompletionCreate,
    WorkCompletionRead,
)
from tests.test_completion_evidence import _generated_evidence
from tests.test_openapi_snapshot import openapi_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS = json.loads(
    (PROJECT_ROOT / "tests/fixtures/completion-evidence-v1.json").read_text(encoding="utf-8")
)
CASES_BY_ID = {case["case_id"]: case for case in CORPUS["cases"]}
RUNTIME_ONLY_CORPUS_CASES = {"duplicate_artifact"}


def _component_validator(component: str) -> Draft202012Validator:
    document = openapi_snapshot()

    def rewrite_references(value: object) -> object:
        if isinstance(value, list):
            return [rewrite_references(item) for item in value]
        if isinstance(value, dict):
            rewritten = {key: rewrite_references(item) for key, item in value.items()}
            reference = rewritten.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                rewritten["$ref"] = reference.replace("#/components/schemas/", "#/$defs/", 1)
            return rewritten
        return value

    definitions = rewrite_references(document["components"]["schemas"])
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/$defs/{component}",
        "$defs": definitions,
    }
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _completion_cursor_schema() -> dict[str, object]:
    operation = openapi_snapshot()["paths"][
        "/api/v1/projects/{project_id}/work-items/{work_item_id}/completion-evidence"
    ]["get"]
    parameter = next(
        item
        for item in operation["parameters"]
        if item["in"] == "query" and item["name"] == "cursor"
    )
    schema = parameter["schema"]
    Draft202012Validator.check_schema(schema)
    return schema


def _base_completion_request() -> dict[str, object]:
    return {
        "expected_version": 1,
        "checkpoint": {
            "prompt": "Completed the requested implementation.",
            "source_client": "pytest",
            "source_session_id": "phase-11-openapi",
        },
    }


def _full_request_case(case: dict[str, Any]) -> dict[str, object]:
    payload = _base_completion_request()
    evidence = case.get("completion_evidence", "__omitted__")
    evidence_case_id = case.get("completion_evidence_case_id")
    if evidence_case_id is not None:
        evidence = deepcopy(CASES_BY_ID[evidence_case_id]["semantic_input"])
    if evidence != "__omitted__":
        payload["completion_evidence"] = evidence
    operation_id = case["client_operation_id"]
    if operation_id != "__omitted__":
        payload["client_operation_id"] = operation_id
    return payload


@pytest.mark.parametrize(
    "case",
    CORPUS["full_request_cases"],
    ids=lambda case: case["case_id"],
)
def test_draft_2020_openapi_agrees_with_shared_full_request_corpus(
    case: dict[str, Any],
):
    payload = _full_request_case(case)
    expected = case["surface_expectations"]["rest_openapi"]
    errors = list(_component_validator("WorkCompletionCreate").iter_errors(payload))
    assert (not errors) is expected, [error.json_path for error in errors]


@pytest.mark.parametrize(
    "case",
    CORPUS["cases"],
    ids=lambda case: case["case_id"],
)
def test_draft_2020_openapi_agrees_with_shared_evidence_corpus(
    case: dict[str, Any],
):
    """Exercise every portable constraint against the language-neutral corpus.

    Draft 2020-12 cannot express uniqueness by a subset of object properties. The
    schema publishes that sole runtime-only rule through ``x-unique-by``; every
    other shared case must agree exactly with strict model validation.
    """
    validator = _component_validator("CompletionEvidenceInput")
    actual = validator.is_valid(case["semantic_input"])
    if case["case_id"] in RUNTIME_ONLY_CORPUS_CASES:
        assert case["valid"] is False
        assert actual is True
    else:
        assert actual is case["valid"], [
            error.json_path for error in validator.iter_errors(case["semantic_input"])
        ]


@pytest.mark.parametrize(
    "case",
    CORPUS["generated_boundaries"],
    ids=lambda case: case["case_id"],
)
def test_draft_2020_openapi_agrees_with_portable_generated_boundaries(
    case: dict[str, Any],
):
    evidence = _generated_evidence(case)
    actual = _component_validator("CompletionEvidenceInput").is_valid(evidence)
    if case.get("error_class") == "aggregate_bytes":
        assert actual is True
        if case["valid"]:
            CompletionEvidenceInput.model_validate(evidence)
        else:
            with pytest.raises(ValueError):
                CompletionEvidenceInput.model_validate(evidence)
    else:
        assert actual is case["valid"]


EMPTY_EVIDENCE_SPELLINGS = (
    "__omitted__",
    {},
    {"verification_results": []},
    {"artifact_references": []},
    {"verification_results": [], "artifact_references": []},
)
OPERATION_ID_SPELLINGS = (
    "__omitted__",
    None,
    "11584ccf-c787-4c6a-bb89-a69a02c1554d",
)


@pytest.mark.parametrize("evidence", EMPTY_EVIDENCE_SPELLINGS)
@pytest.mark.parametrize("operation_id", OPERATION_ID_SPELLINGS)
def test_openapi_allows_every_empty_evidence_and_optional_operation_id_spelling(
    evidence: object,
    operation_id: object,
):
    payload = _base_completion_request()
    if evidence != "__omitted__":
        payload["completion_evidence"] = evidence
    if operation_id != "__omitted__":
        payload["client_operation_id"] = operation_id
    assert _component_validator("WorkCompletionCreate").is_valid(payload)


@pytest.mark.parametrize(
    "evidence",
    (
        CASES_BY_ID["passed_command"]["semantic_input"],
        CASES_BY_ID["all_artifact_types"]["semantic_input"],
        {
            "verification_results": CASES_BY_ID["passed_command"]["semantic_input"][
                "verification_results"
            ],
            "artifact_references": CASES_BY_ID["all_artifact_types"]["semantic_input"][
                "artifact_references"
            ],
        },
    ),
    ids=("results", "artifacts", "mixed"),
)
@pytest.mark.parametrize(
    ("operation_id", "valid"),
    (
        ("__omitted__", False),
        (None, False),
        ("not-a-uuid", False),
        ("11584ccf-c787-4c6a-bb89-a69a02c1554d", True),
    ),
)
def test_openapi_nonempty_evidence_requires_a_nonnull_valid_uuid(
    evidence: object,
    operation_id: object,
    valid: bool,
):
    payload = _base_completion_request()
    payload["completion_evidence"] = deepcopy(evidence)
    if operation_id != "__omitted__":
        payload["client_operation_id"] = operation_id
    assert _component_validator("WorkCompletionCreate").is_valid(payload) is valid


def test_openapi_exposes_executable_evidence_bounds_and_discriminated_grammars():
    components = openapi_snapshot()["components"]["schemas"]
    completion = components["CompletionEvidenceInput"]
    assert completion["x-utf8-aggregate-max-bytes"] == 32768
    assert len(completion["anyOf"]) == 21
    assert completion["properties"]["artifact_references"]["x-unique-by"] == [
        "artifact_type",
        "reference",
    ]

    command = components["CommandVerificationInput"]
    assert len(command["oneOf"]) == 3
    assert {tuple(branch.get("required", ())) for branch in command["oneOf"]} == {
        ("exit_code",),
        (),
    }
    assert command["properties"]["name"]["maxLength"] == 200
    assert command["properties"]["name"]["x-utf8-max-bytes"] == 800
    assert command["properties"]["summary"]["maxLength"] == 4000
    assert command["properties"]["command"]["maxLength"] == 4096
    observed_at = components["ObservationVerificationRead"]["properties"]["observed_at"]
    assert observed_at["format"] == "date-time"
    assert observed_at["minLength"] == 20
    assert observed_at["maxLength"] == 27
    assert observed_at["pattern"].endswith("Z(?![\\s\\S])")

    artifact = components["ArtifactReferenceInput"]
    assert len(artifact["allOf"]) == 4
    assert artifact["allOf"][3]["then"]["properties"]["reference"]["maxLength"] == 2000

    work_completion = components["WorkCompletionCreate"]
    assert work_completion["properties"]["expected_version"]["maximum"] == (
        COMPLETION_EXPECTED_VERSION_MAX
    )
    assert work_completion["properties"]["completion_evidence"]["$ref"] == (
        "#/components/schemas/CompletionEvidenceInput"
    )
    assert "completion_evidence" not in work_completion["required"]

    page = components["CompletionEvidencePage"]
    for field in ("total", "structured_completion_total"):
        maximum = page["properties"][field]["maximum"]
        assert isinstance(maximum, int)
        assert maximum == COMPLETION_EVENT_ID_MAX

    cursor = _completion_cursor_schema()
    assert cursor["minLength"] == 1
    assert cursor["maxLength"] == 4096
    assert "{4}" in cursor["pattern"]


@pytest.mark.parametrize(
    ("cursor", "valid"),
    (
        ("AA", True),
        ("AAA", True),
        ("AAAA", True),
        ("A", False),
        ("AAAAA", False),
        ("A" * 4096, True),
        ("A" * 4097, False),
    ),
)
def test_cursor_openapi_and_runtime_share_unpadded_base64url_grammar(
    cursor: str, valid: bool
):
    payload = {"cursor": cursor}
    assert Draft202012Validator(_completion_cursor_schema()).is_valid(cursor) is valid
    if valid:
        CompletionEvidenceListQuery.model_validate(payload)
    else:
        with pytest.raises(ValueError):
            CompletionEvidenceListQuery.model_validate(payload)


def test_completion_request_rejects_versions_that_cannot_be_incremented():
    validator = _component_validator("WorkCompletionCreate")
    maximum = _base_completion_request()
    maximum["expected_version"] = COMPLETION_EXPECTED_VERSION_MAX
    assert validator.is_valid(maximum)
    WorkCompletionCreate.model_validate(maximum)

    overflow = deepcopy(maximum)
    overflow["expected_version"] = COMPLETION_EXPECTED_VERSION_MAX + 1
    assert not validator.is_valid(overflow)
    with pytest.raises(ValueError):
        WorkCompletionCreate.model_validate(overflow)


@pytest.mark.parametrize(
    ("operation_id", "valid"),
    (
        ("11584ccf-c787-4c6a-bb89-a69a02c1554d", True),
        ("11584CCF-C787-4C6A-BB89-A69A02C1554D", True),
        ("11584ccfc7874c6abb89a69a02c1554d", True),
        ("{11584ccf-c787-4c6a-bb89-a69a02c1554d}", True),
        ("urn:uuid:11584ccf-c787-4c6a-bb89-a69a02c1554d", True),
        ("URN:UUID:11584ccf-c787-4c6a-bb89-a69a02c1554d", False),
        ("urn:uuid:11584ccfc7874c6abb89a69a02c1554d", False),
        ("{11584ccfc7874c6abb89a69a02c1554d}", False),
    ),
)
def test_completion_operation_id_json_spellings_have_runtime_openapi_parity(
    operation_id: str, valid: bool
):
    request = _base_completion_request()
    request["completion_evidence"] = deepcopy(
        CASES_BY_ID["passed_command"]["semantic_input"]
    )
    request["client_operation_id"] = operation_id
    assert _component_validator("WorkCompletionCreate").is_valid(request) is valid
    if valid:
        WorkCompletionCreate.model_validate(request)
    else:
        with pytest.raises(ValueError):
            WorkCompletionCreate.model_validate(request)


@pytest.mark.parametrize(
    "reference",
    (
        "https://example.test:65536/path",
        "https://example.test:0443/path",
        "https://example.test:/path",
        "https://[::::]/path",
        "https://[::ffff:192.0.2.1]/path",
        "https://[2001:0db8::1]/runs/1",
        "https://[2001:0db8::1]:8443/runs/1",
        "https://[0:0:0:0:0:0:0:1]/runs/1",
        "https://[0000::1]/runs/1",
        "https://127.0.0.01/path",
        "https://" + "a" * 254 + "/path",
        "https://example.test/<path>",
        "https://example.test/%2e%2e",
        "https://example.test/path?",
        "https://example.test/path#",
        "https://example.test/path\n",
    ),
)
def test_adversarial_artifact_urls_fail_runtime_and_openapi(reference: str):
    payload = {
        "artifact_references": [
            {
                "artifact_type": "external_issue",
                "label": "Issue",
                "reference": reference,
            }
        ]
    }
    with pytest.raises(ValueError):
        CompletionEvidenceInput.model_validate(payload)
    assert not _component_validator("CompletionEvidenceInput").is_valid(payload)


@pytest.mark.parametrize(
    "reference",
    (
        "a/",
        "a//b",
        "a/../b",
        "a/.\n",
    ),
)
def test_adversarial_repository_paths_fail_runtime_and_openapi(reference: str):
    payload = {
        "artifact_references": [
            {
                "artifact_type": "repository_path",
                "label": "Path",
                "reference": reference,
            }
        ]
    }
    with pytest.raises(ValueError):
        CompletionEvidenceInput.model_validate(payload)
    assert not _component_validator("CompletionEvidenceInput").is_valid(payload)


@pytest.mark.parametrize(
    "observed_at",
    (
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59-14:00",
        "2024-00-00T24:00:00Z",
        "2024-01-01T24:00:00Z",
        "2024-01-01T00:00:00Z\n",
    ),
)
def test_adversarial_observed_times_fail_runtime_and_openapi(observed_at: str):
    payload = {
        "verification_results": [
            {
                "verification_type": "observation",
                "name": "Review",
                "outcome": "passed",
                "summary": "Reviewed.",
                "observed_at": observed_at,
            }
        ]
    }
    with pytest.raises(ValueError):
        CompletionEvidenceInput.model_validate(payload)
    assert not _component_validator("CompletionEvidenceInput").is_valid(payload)


@pytest.mark.parametrize(
    ("observed_at", "canonical"),
    (
        ("0001-01-01T14:00:00+14:00", "0001-01-01T00:00:00Z"),
        ("0001-01-01T00:01:00+00:01", "0001-01-01T00:00:00Z"),
        ("9999-12-31T00:00:00-14:00", "9999-12-31T14:00:00Z"),
        ("9999-12-31T23:58:00-00:01", "9999-12-31T23:59:00Z"),
    ),
)
def test_representable_boundary_offsets_pass_runtime_and_openapi(
    observed_at: str,
    canonical: str,
):
    payload = {
        "verification_results": [
            {
                "verification_type": "observation",
                "name": "Boundary review",
                "outcome": "passed",
                "summary": "The represented UTC instant stays within range.",
                "observed_at": observed_at,
            }
        ]
    }
    parsed = CompletionEvidenceInput.model_validate(payload)
    assert parsed.model_dump(mode="json")["verification_results"][0]["observed_at"] == canonical
    assert _component_validator("CompletionEvidenceInput").is_valid(payload)


def _base_completion_response() -> dict[str, object]:
    work_item_id = "11111111-1111-4111-8111-111111111111"
    return {
        "work_item": {
            "id": work_item_id,
            "project_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "title": "Strict response checkpoint",
            "summary": "The completion response exposes only a completion checkpoint.",
            "status": "done",
            "priority": 50,
            "initial_checkpoint_id": "33333333-3333-4333-8333-333333333333",
            "version": 2,
            "created_at": "2026-09-04T18:00:00Z",
            "updated_at": "2026-09-04T18:01:00Z",
        },
        "checkpoint": {
            "id": "22222222-2222-4222-8222-222222222222",
            "work_item_id": work_item_id,
            "kind": "completion",
            "prompt": "Completed and verified.",
            "source_client": "pytest",
            "source_session_id": "strict-response",
            "source_model": None,
            "source_session_url": None,
            "repository_branch": None,
            "verified_against": None,
            "tags": [],
            "source_metadata": {},
            "migration_origin": None,
            "legacy_record_id": None,
            "created_at": "2026-09-04T18:01:00Z",
        },
    }


def test_completion_response_checkpoint_kind_is_executable_in_openapi():
    response = _base_completion_response()
    WorkCompletionRead.model_validate(response)
    validator = _component_validator("WorkCompletionRead")
    assert validator.is_valid(response)

    wrong_kind = deepcopy(response)
    wrong_kind["checkpoint"]["kind"] = "context"
    with pytest.raises(ValueError):
        WorkCompletionRead.model_validate(wrong_kind)
    assert not validator.is_valid(wrong_kind)


def test_history_response_schema_rejects_runtime_only_shapes():
    history = json.loads(
        (PROJECT_ROOT / "examples/completion-evidence-history.json").read_text(encoding="utf-8")
    )
    validator = _component_validator("CompletionEvidencePage")
    mutations = []

    wrong_kind = deepcopy(history)
    wrong_kind["items"][0]["completion_checkpoint"]["kind"] = "context"
    mutations.append(wrong_kind)

    high_event = deepcopy(history)
    high_event["items"][0]["completion_event_id"] = "9223372036854775807"
    mutations.append(high_event)

    sparse_null = deepcopy(history)
    sparse_null["items"][1]["verification_results"][0]["observed_at_commit"] = None
    mutations.append(sparse_null)

    noncanonical_time = deepcopy(history)
    noncanonical_time["items"][1]["verification_results"][0]["created_at"] = (
        "2026-09-03T14:04:12.123456-04:00"
    )
    mutations.append(noncanonical_time)

    for field, value in (
        ("total", COMPLETION_EVENT_ID_MAX + 1),
        ("structured_completion_total", COMPLETION_EVENT_ID_MAX + 2),
    ):
        oversized_total = deepcopy(history)
        oversized_total[field] = value
        mutations.append(oversized_total)

    for mutation in mutations:
        with pytest.raises(ValueError):
            CompletionEvidencePage.model_validate(mutation)
        assert not validator.is_valid(mutation)


def test_nonempty_payload_response_schema_rejects_two_empty_arrays():
    assert not _component_validator("CompletionEvidencePayloadRead").is_valid(
        {"verification_results": [], "artifact_references": []}
    )


def test_checked_examples_validate_against_strict_models_and_openapi():
    without_evidence = json.loads(
        (PROJECT_ROOT / "examples/completion-without-evidence.json").read_text(encoding="utf-8")
    )
    history = json.loads(
        (PROJECT_ROOT / "examples/completion-evidence-history.json").read_text(encoding="utf-8")
    )

    WorkCompletionCreate.model_validate(without_evidence)
    CompletionEvidencePage.model_validate(history)
    assert _component_validator("WorkCompletionCreate").is_valid(without_evidence)
    assert _component_validator("CompletionEvidencePage").is_valid(history)
