"""Focused unit regressions for adversarial duplicate-handling review findings."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import ValidationError

from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import (
    CanonicalWorkProjection,
    DuplicateMergeEligibility,
    WorkItemDetailRead,
    WorkMergeResult,
)
from mnemonic_api.semantic import EmbeddingCandidate, rank_embedding_candidates
from mnemonic_api.services.duplicates import validate_project_duplicate_graph
from tests.test_client_operations import response_vector_cases


class UnexpectedEmbedder:
    def embed_documents(self, texts):
        raise AssertionError(f"Cached candidates must not be embedded: {list(texts)}")

    def embed_query(self, text):
        raise AssertionError(f"The ranking helper must not embed its query: {text}")


def test_semantic_equal_score_and_updated_at_ties_use_uuid_ascending() -> None:
    project_id = UUID("10000000-0000-0000-0000-000000000001")
    updated_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
    item_ids = [
        UUID("20000000-0000-0000-0000-000000000003"),
        UUID("20000000-0000-0000-0000-000000000001"),
        UUID("20000000-0000-0000-0000-000000000002"),
    ]
    candidates = [
        EmbeddingCandidate(
            work_item=SimpleNamespace(
                id=item_id,
                project_id=project_id,
                version=1,
                updated_at=updated_at,
            ),
            text=f"Candidate {item_id}",
            digest=item_id.hex,
            cached_vector=(1.0, 0.0),
        )
        for item_id in item_ids
    ]

    ranked, updates = rank_embedding_candidates(
        candidates,
        lexical_ids=[],
        query_vector=(1.0, 0.0),
        embedder=UnexpectedEmbedder(),
    )

    assert ranked == sorted(item_ids, key=lambda item_id: item_id.int)
    assert updates == []


def _valid_merge_response() -> dict:
    return deepcopy(
        next(
            source
            for kind, source, _expected in response_vector_cases()
            if kind == "merge_work"
        )
    )


@pytest.mark.parametrize("value", (0, 1, "false", "true"))
def test_duplicate_projection_models_reject_coercible_booleans(value: object) -> None:
    root_id = UUID("20000000-0000-0000-0000-000000000001")
    pointer = {"id": root_id, "title": "Canonical work", "status": "pending"}
    with pytest.raises(ValidationError):
        CanonicalWorkProjection.model_validate(
            {
                "is_duplicate": value,
                "direct_destination": None,
                "canonical_work_item": pointer,
                "path": [],
                "duplicate_member_count": 0,
            }
        )

    with pytest.raises(ValidationError):
        DuplicateMergeEligibility.model_validate(
            {
                "incident_blocks_count": 0,
                "incident_parent_child_count": 0,
                "has_unresolved_gate": value,
                "source_lease_state": "none",
            }
        )

    merge_response = _valid_merge_response()
    merge_response["supporting_relationship_created"] = value
    with pytest.raises(ValidationError):
        WorkMergeResult.model_validate(merge_response)


def _pointer(identifier: int, title: str, status: str = "pending") -> dict[str, object]:
    return {"id": UUID(int=identifier), "title": title, "status": status}


def _valid_alias_projection() -> dict[str, object]:
    direct = _pointer(2, "Direct destination")
    canonical = _pointer(3, "Canonical root", "deferred")
    return {
        "is_duplicate": True,
        "direct_destination": direct,
        "canonical_work_item": canonical,
        "path": [deepcopy(direct), deepcopy(canonical)],
        "duplicate_member_count": 2,
    }


def _work_item(
    identifier: int,
    title: str,
    status: str = "pending",
) -> dict[str, object]:
    timestamp = datetime(2026, 9, 2, 12, tzinfo=UTC)
    return {
        "id": UUID(int=identifier),
        "project_id": UUID(int=100),
        "title": title,
        "summary": "Durable identity fixture.",
        "status": status,
        "priority": 0,
        "initial_checkpoint_id": UUID(int=identifier + 1000),
        "version": 1,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_alias_projection_rejects_member_count_shorter_than_its_path() -> None:
    payload = _valid_alias_projection()
    assert CanonicalWorkProjection.model_validate(payload).duplicate_member_count == 2
    payload["duplicate_member_count"] = 1

    with pytest.raises(ValidationError, match="requires a direct destination and path"):
        CanonicalWorkProjection.model_validate(payload)


@pytest.mark.parametrize(
    ("path_index", "field", "contradiction", "message"),
    (
        (0, "title", "Contradictory destination title", "begin with the direct destination"),
        (-1, "status", "done", "end with the canonical work item"),
    ),
)
def test_alias_projection_binds_full_endpoint_pointers(
    path_index: int,
    field: str,
    contradiction: str,
    message: str,
) -> None:
    payload = _valid_alias_projection()
    CanonicalWorkProjection.model_validate(payload)
    path = payload["path"]
    assert isinstance(path, list)
    endpoint = path[path_index]
    assert isinstance(endpoint, dict)
    endpoint[field] = contradiction

    with pytest.raises(ValidationError, match=message):
        CanonicalWorkProjection.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "contradiction"),
    (("title", "Contradictory root title"), ("status", "done")),
)
def test_root_detail_binds_its_full_canonical_identity(
    field: str,
    contradiction: str,
) -> None:
    work_item = _work_item(1, "Canonical root")
    pointer = _pointer(1, "Canonical root")
    payload = {
        "work_item": work_item,
        "canonical": {
            "is_duplicate": False,
            "direct_destination": None,
            "canonical_work_item": pointer,
            "path": [],
            "duplicate_member_count": 0,
        },
    }
    WorkItemDetailRead.model_validate(payload)
    pointer[field] = contradiction

    with pytest.raises(ValidationError, match="root must point to itself"):
        WorkItemDetailRead.model_validate(payload)


def test_alias_detail_rejects_requested_identity_anywhere_in_path() -> None:
    requested = _work_item(1, "Requested alias")
    direct = _pointer(2, "Direct destination")
    requested_pointer = _pointer(1, "Requested alias")
    canonical = _pointer(3, "Canonical root")
    projection = {
        "is_duplicate": True,
        "direct_destination": direct,
        "canonical_work_item": canonical,
        "path": [direct, requested_pointer, canonical],
        "duplicate_member_count": 3,
    }
    CanonicalWorkProjection.model_validate(projection)

    with pytest.raises(ValidationError, match="cannot contain its requested source"):
        WorkItemDetailRead.model_validate(
            {"work_item": requested, "canonical": projection}
        )


def _assert_graph_rejected(rows: list[tuple[UUID, UUID, int]]) -> None:
    project_id = UUID("10000000-0000-0000-0000-000000000001")
    work_item_ids = {endpoint for row in rows for endpoint in row[:2]}
    work_items = [
        SimpleNamespace(
            id=work_item_id,
            project_id=project_id,
            deleted_at=None,
            title=f"Work {work_item_id}",
            status="pending",
        )
        for work_item_id in work_item_ids
    ]
    database = Mock()
    database.execute.return_value = rows
    database.scalars.return_value = work_items

    with pytest.raises(ApplicationError) as captured:
        validate_project_duplicate_graph(database, project_id)

    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "duplicate_graph_invalid"


def test_graph_loader_rejects_a_cycle() -> None:
    first = UUID(int=1)
    second = UUID(int=2)
    _assert_graph_rejected([(first, second, 1), (second, first, 2)])


def test_graph_loader_rejects_a_path_longer_than_fifty_edges() -> None:
    work_item_ids = [UUID(int=index) for index in range(1, 53)]
    _assert_graph_rejected(
        [
            (source_id, work_item_ids[index + 1], index + 1)
            for index, source_id in enumerate(work_item_ids[:-1])
        ]
    )
