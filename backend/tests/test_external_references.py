"""Shared strict reference grammar and permanent-operation intent contract."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from mnemonic_api.external_references import (
    ExternalLabel,
    ExternalReference,
    ExternalReferences,
    ExternalURL,
    ObservationTime,
)
from mnemonic_api.schemas import WorkItemCreate, WorkItemPatch
from mnemonic_api.services.client_operations import (
    canonical_request_bytes,
    operation_spec,
    prepare_client_operation,
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2] / "tests/fixtures/external-record-contract-v1.json"
    ).read_text()
)
REFERENCE = {"url": "https://example.com/issues/1", "kind": "tracked-by", "state": "open"}


@pytest.mark.parametrize("case", FIXTURE["url_cases"])
def test_url_contract(case):
    if case["valid"]:
        assert TypeAdapter(ExternalURL).validate_python(case["value"]) == case["value"]
    else:
        with pytest.raises(ValidationError):
            TypeAdapter(ExternalURL).validate_python(case["value"])


@pytest.mark.parametrize("case", FIXTURE["label_cases"])
def test_label_contract(case):
    if case["valid"]:
        assert TypeAdapter(ExternalLabel).validate_python(case["value"]) == case["value"]
    else:
        with pytest.raises(ValidationError):
            TypeAdapter(ExternalLabel).validate_python(case["value"])


@pytest.mark.parametrize("case", FIXTURE["timestamp_cases"])
def test_timestamp_contract(case):
    if case["normalized"] is not None:
        assert TypeAdapter(ObservationTime).validate_python(case["value"]) == case["normalized"]
    else:
        with pytest.raises(ValidationError):
            TypeAdapter(ObservationTime).validate_python(case["value"])


@pytest.mark.parametrize(
    "changes",
    [
        {"url": 123},
        {"kind": None},
        {"state": True},
        {"label": None},
        {"state_observed_at": None},
        {"unknown": "field"},
        {"label": "\ud800"},
    ],
)
def test_reference_rejects_null_coercion_unknown_unicode(changes):
    with pytest.raises(ValidationError):
        ExternalReference.model_validate({**REFERENCE, **changes})


def test_lists_are_bounded_unique_and_ordered():
    adapter = TypeAdapter(ExternalReferences)
    values = [{**REFERENCE, "url": f"https://example.com/{index}"} for index in range(10)]
    assert [item.url for item in adapter.validate_python(values)] == [v["url"] for v in values]
    for invalid in [None, [REFERENCE, REFERENCE], [*values, REFERENCE]]:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_omission_clear_and_creation_empty_fingerprint():
    project_id = uuid4()
    target = {"work_item_id": str(uuid4())}
    base = {"expected_version": 1, "title": "Title"}
    omitted = WorkItemPatch.model_validate(base)
    cleared = WorkItemPatch.model_validate({**base, "external_references": []})
    assert "external_references" not in omitted.model_dump(mode="json")
    assert cleared.model_dump(mode="json", exclude_unset=True)["external_references"] == []
    spec = operation_spec("update_work")
    assert canonical_request_bytes(spec, project_id, target, omitted) != canonical_request_bytes(
        spec,
        project_id,
        target,
        cleared,
    )
    create = {
        "title": "Title",
        "summary": "Summary",
        "initial_checkpoint": {
            "prompt": "Prompt",
            "source_client": "pytest",
            "source_session_id": "session",
        },
    }
    spec = operation_spec("create_work")
    assert canonical_request_bytes(spec, project_id, {}, WorkItemCreate.model_validate(create)) == (
        canonical_request_bytes(
            spec,
            project_id,
            {},
            WorkItemCreate.model_validate(
                {
                    **create,
                    "external_references": [],
                }
            ),
        )
    )
    with pytest.raises(ValidationError):
        WorkItemPatch.model_validate({**base, "external_references": None})


def test_durable_reference_secret_substrings_are_rejected():
    operation_id = uuid4()
    for forbidden in ("request-bearer-secret", str(operation_id).upper(), operation_id.hex):
        payload = WorkItemPatch.model_validate(
            {
                "expected_version": 1,
                "actor": {"actor_client": "pytest", "actor_session_id": "session"},
                "client_operation_id": operation_id,
                "external_references": [{**REFERENCE, "label": "prefix " + forbidden + " suffix"}],
            }
        )
        from mnemonic_api.errors import ApplicationError

        with pytest.raises(ApplicationError, match="credential|capability"):
            prepare_client_operation(
                "update_work",
                uuid4(),
                {"work_item_id": uuid4()},
                payload,
                known_secret_values=["request-bearer-secret"],
            )


def test_duplicate_snapshot_owns_reference_values():
    from datetime import UTC, datetime

    from mnemonic_api.models import WorkItem
    from mnemonic_api.services.duplicate_suggestions import _work_snapshot

    work = WorkItem(
        id=uuid4(),
        project_id=uuid4(),
        title="Captured title",
        summary="Summary",
        status="pending",
        version=1,
        updated_at=datetime.now(UTC),
        external_references=[dict(REFERENCE)],
    )
    captured = _work_snapshot(work)
    work.external_references[0]["state"] = "closed"
    work.external_references.append({**REFERENCE, "url": REFERENCE["url"] + "?new"})
    assert captured.external_references == (REFERENCE,)
