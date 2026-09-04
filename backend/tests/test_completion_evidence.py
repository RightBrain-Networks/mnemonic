"""Language-neutral Phase 11 completion-evidence contract vectors."""

import ipaddress
import json
import re
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

import mnemonic_api.schemas as schemas_module
from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import (
    CompletionEvidenceInput,
    CompletionEvidencePage,
    WorkCompletionCreate,
    artifact_https_url,
    completion_evidence_text_bytes,
)
from mnemonic_api.services.client_operations import prepare_client_operation

PROJECT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORK_ID = UUID("20000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("40000000-0000-0000-0000-000000000001")
CORPUS_PATH = Path(__file__).resolve().parents[2] / "tests/fixtures/completion-evidence-v1.json"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CONCRETE_CASES = CORPUS["cases"]


def _completion_request(
    evidence: object = ...,
    *,
    operation_id: UUID | None | object = OPERATION_ID,
    lease_token: str | None = None,
) -> WorkCompletionCreate:
    payload: dict[str, object] = {
        "expected_version": 1,
        "checkpoint": {
            "prompt": "Completed the requested implementation.",
            "source_client": "pytest",
            "source_session_id": "phase-11-unit",
        },
    }
    if evidence is not ...:
        payload["completion_evidence"] = evidence
    if operation_id is not ...:
        payload["client_operation_id"] = operation_id
    if lease_token is not None:
        payload["lease_token"] = lease_token
    return WorkCompletionCreate.model_validate(payload)


@pytest.mark.parametrize(
    "case",
    [case for case in CONCRETE_CASES if case["valid"]],
    ids=lambda case: case["case_id"],
)
def test_shared_valid_vectors_have_the_exact_canonical_form(case: dict[str, object]):
    request = _completion_request(deepcopy(case["semantic_input"]))
    canonical = case["canonical_output"]
    if canonical is None:
        assert request.completion_evidence is None
        assert "completion_evidence" not in request.model_dump(mode="json")
    else:
        assert request.completion_evidence is not None
        assert request.completion_evidence.model_dump(mode="json") == canonical


@pytest.mark.parametrize(
    "case",
    [case for case in CONCRETE_CASES if not case["valid"]],
    ids=lambda case: case["case_id"],
)
def test_shared_invalid_vectors_fail_below_the_evidence_field(case: dict[str, object]):
    with pytest.raises(ValidationError) as captured:
        _completion_request(deepcopy(case["semantic_input"]))

    assert captured.value.errors()
    assert all(error["loc"][:1] == ("completion_evidence",) for error in captured.value.errors())


@pytest.mark.parametrize(
    ("reference", "valid"),
    (
        ("https://[2001:db8::1]/runs/1", True),
        ("https://[2001:db8::1]:8443/runs/1", True),
        ("https://[2001:0db8::1]/runs/1", False),
        ("https://[2001:0db8::1]:8443/runs/1", False),
        ("https://[0:0:0:0:0:0:0:1]/runs/1", False),
        ("https://[0000::1]/runs/1", False),
    ),
)
def test_artifact_ipv6_urls_require_exact_lowercase_compressed_spelling(
    reference: str,
    valid: bool,
):
    if valid:
        assert artifact_https_url(reference) == reference
    else:
        with pytest.raises(ValueError):
            artifact_https_url(reference)


def test_ipv6_schema_pattern_exactly_matches_runtime_canonicality_for_every_zero_mask():
    nonzero_groups = ("1", "2", "3", "4", "5", "6", "7", "8")

    def accepts(hostname: str, *, port: str = "") -> tuple[bool, bool]:
        schema_accepts = (
            re.fullmatch(schemas_module._IPV6_SCHEMA_PATTERN, f"[{hostname}]") is not None
        )
        try:
            artifact_https_url(f"https://[{hostname}]{port}/runs/1")
        except ValueError:
            runtime_accepts = False
        else:
            runtime_accepts = True
        return schema_accepts, runtime_accepts

    for mask in range(256):
        expanded = ":".join(
            "0" if mask & (1 << position) else nonzero_groups[position]
            for position in range(8)
        )
        canonical = str(ipaddress.IPv6Address(expanded))
        for hostname in {expanded, canonical}:
            expected = hostname == canonical
            assert accepts(hostname) == (expected, expected), (mask, hostname)
            assert accepts(hostname, port=":8443") == (expected, expected), (
                mask,
                hostname,
            )

    for hostname in (
        "2001:db8::1:2:3:4:5",
        "0::1",
        "1::0",
        "2001:0::1",
        "2001::0:1",
        "2001:0:0:1::1:1",
        "2001:0:0:1:2:3:4:5",
        "2001::1::1",
        "1:2:3:4:5:6:7:8:9",
        "2001:DB8::1",
        "2001:0db8::1",
        "::ffff:192.0.2.1",
        "fe80::1%eth0",
    ):
        assert accepts(hostname) == (False, False), hostname
        assert accepts(hostname, port=":8443") == (False, False), hostname

    for hostname in (
        "::",
        "::1",
        "1::",
        "0:1::",
        "::1:0",
        "2001:db8:0:1:2:3:4:5",
        "2001::1:0:0:1:1",
    ):
        assert accepts(hostname) == (True, True), hostname
        assert accepts(hostname, port=":8443") == (True, True), hostname


def _generated_evidence(case: dict[str, object]) -> dict[str, object]:
    generator = case["generator"]
    if generator == "repeat_valid_observation":
        result = {
            "verification_type": "observation",
            "name": "Review",
            "outcome": "passed",
            "summary": "Reviewed.",
        }
        return {"verification_results": [deepcopy(result) for _ in range(case["count"])]}
    if generator == "fill_summary_to_aggregate_utf8_bytes":
        first = {
            "verification_type": "command",
            "name": "n",
            "outcome": "passed",
            "summary": "\U00010000" * 4000,
            "command": "\U00010000" * 4096,
            "exit_code": 0,
        }
        second = {
            "verification_type": "observation",
            "name": "n",
            "outcome": "passed",
            "summary": "s",
        }
        provisional = CompletionEvidenceInput.model_validate(
            {"verification_results": [first, second]}
        )
        current = completion_evidence_text_bytes(
            provisional.verification_results, provisional.artifact_references
        )
        second["summary"] = "s" * (case["bytes"] - current + 1)
        return {"verification_results": [first, second]}
    if generator == "fill_text_with_u10000":
        return {
            "verification_results": [
                {
                    "verification_type": "observation",
                    "name": "Unicode boundary",
                    "outcome": "passed",
                    "summary": "\U00010000" * case["characters"],
                }
            ]
        }
    raise AssertionError(f"Unknown corpus generator: {generator}")


@pytest.mark.parametrize(
    "case",
    CORPUS["generated_boundaries"],
    ids=lambda case: case["case_id"],
)
def test_shared_generated_boundaries(case: dict[str, object]):
    evidence = _generated_evidence(case)
    if case["valid"]:
        parsed = CompletionEvidenceInput.model_validate(evidence)
        if case["generator"] == "fill_summary_to_aggregate_utf8_bytes":
            assert (
                completion_evidence_text_bytes(
                    parsed.verification_results, parsed.artifact_references
                )
                == case["bytes"]
            )
        elif case["generator"] == "fill_text_with_u10000":
            summary = parsed.verification_results[0].summary
            assert len(summary) == case["characters"]
            assert len(summary.encode("utf-8")) == case["bytes"]
        return
    with pytest.raises(ValidationError):
        CompletionEvidenceInput.model_validate(evidence)


def test_shared_full_request_vectors_enforce_conditional_operation_identity():
    cases_by_id = {case["case_id"]: case for case in CONCRETE_CASES}
    for case in CORPUS["full_request_cases"]:
        evidence = case.get("completion_evidence", ...)
        if evidence == "__omitted__":
            evidence = ...
        evidence_case = case.get("completion_evidence_case_id")
        if evidence_case is not None:
            evidence = deepcopy(cases_by_id[evidence_case]["semantic_input"])
        operation_id = case["client_operation_id"]
        if operation_id == "__omitted__":
            operation_id = ...
        expected = case["surface_expectations"]["rest_openapi"]
        if expected:
            _completion_request(evidence, operation_id=operation_id)
        else:
            with pytest.raises(ValidationError):
                _completion_request(evidence, operation_id=operation_id)


def test_empty_evidence_omission_and_equivalent_times_have_identical_fingerprints():
    target = {"work_item_id": WORK_ID}
    omitted = prepare_client_operation(
        "complete_work", PROJECT_ID, target, _completion_request()
    )
    empty = prepare_client_operation(
        "complete_work", PROJECT_ID, target, _completion_request({})
    )
    assert omitted.canonical_bytes == empty.canonical_bytes

    cases = {case["case_id"]: case for case in CONCRETE_CASES}
    short = prepare_client_operation(
        "complete_work",
        PROJECT_ID,
        target,
        _completion_request(cases["timestamp_fraction_canonicalization"]["semantic_input"]),
    )
    long = prepare_client_operation(
        "complete_work",
        PROJECT_ID,
        target,
        _completion_request(cases["timestamp_fraction_six_digits"]["semantic_input"]),
    )
    assert short.canonical_bytes == long.canonical_bytes


@pytest.mark.parametrize(
    "spelling",
    (
        str(OPERATION_ID),
        str(OPERATION_ID).upper(),
        OPERATION_ID.hex,
        OPERATION_ID.hex.upper(),
        "{" + str(OPERATION_ID).upper() + "}",
        "urn:uuid:" + str(OPERATION_ID).upper(),
    ),
)
def test_operation_uuid_substrings_are_rejected_from_nested_evidence(spelling: str):
    request = _completion_request(
        {
            "verification_results": [
                {
                    "verification_type": "observation",
                    "name": "Receipt safety",
                    "outcome": "passed",
                    "summary": f"prefix-{spelling}-suffix",
                }
            ]
        }
    )
    with pytest.raises(ApplicationError) as captured:
        prepare_client_operation(
            "complete_work", PROJECT_ID, {"work_item_id": WORK_ID}, request
        )
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert spelling not in str(captured.value.detail)


@pytest.mark.parametrize("secret_source", ["known", "lease"])
def test_capability_substrings_are_rejected_from_nested_evidence(secret_source: str):
    secret = "secret-capability-value"
    request = _completion_request(
        {
            "artifact_references": [
                {
                    "artifact_type": "branch",
                    "label": f"prefix-{secret}-suffix",
                    "reference": "work/phase11",
                }
            ]
        },
        lease_token=secret if secret_source == "lease" else None,
    )
    known = [secret] if secret_source == "known" else []
    with pytest.raises(ApplicationError) as captured:
        prepare_client_operation(
            "complete_work",
            PROJECT_ID,
            {"work_item_id": WORK_ID},
            request,
            known_secret_values=known,
        )
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert secret not in str(captured.value.detail)


@pytest.mark.parametrize(
    "change",
    (
        {"items": [], "total": 1, "as_of_completion_event_id": "1"},
        {"items": [], "total": 0, "as_of_completion_event_id": "1"},
        {"items": [], "total": 0, "structured_completion_total": 1},
        {"items": [], "total": 0, "next_cursor": "cursor"},
    ),
)
def test_completion_page_rejects_locally_incoherent_assembly(
    change: dict[str, object],
):
    payload: dict[str, object] = {
        "work_item_id": WORK_ID,
        "work_version": 1,
        "lifecycle_status": "pending",
        "is_duplicate": False,
        "canonical_work_item_id": WORK_ID,
        "current_completion_checkpoint_id": None,
        "as_of_completion_event_id": None,
        "items": [],
        "total": 0,
        "structured_completion_total": 0,
        "limit": 10,
        "next_cursor": None,
        **change,
    }
    with pytest.raises(ValidationError):
        CompletionEvidencePage.model_validate(payload)


def test_database_archives_preserve_phase11_acl_contract():
    backup = (REPOSITORY_ROOT / "scripts/database/backup.sh").read_text(encoding="utf-8")
    restore = (REPOSITORY_ROOT / "scripts/database/restore.sh").read_text(encoding="utf-8")

    assert "--no-acl" not in backup
    assert "--no-acl" not in restore
    assert "pg_dump --format=custom --no-owner --file=" in backup
    assert "pg_restore --no-owner --exit-on-error --file=-" in restore
