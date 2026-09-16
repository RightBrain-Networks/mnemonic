"""Zero-hit terms retain source scope, access controls and exact document counts."""

from uuid import uuid4

import pytest

from .test_artifact_search_postgres import extract
from .test_artifact_search_postgres import search as artifact_search
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_unified_search_postgres import mixed as mixed
from .test_unified_search_postgres import search

pytestmark = pytest.mark.postgres


def counts(result):
    return {item["term"]: item["matches"] for item in result["term_diagnostics"]}


def test_default_conjunction_never_opens_transcripts(api, project, mixed, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Default conjunctive search must not load or search transcripts")

    monkeypatch.setattr("mnemonic_api.services.search.transcript_source", forbidden)
    result = search(api, project, q="needle absentword", fulltext=True)
    assert result["total"] == 0
    assert result["search_scope"]["searched_facets"] == ["work_items", "artifacts"]
    assert result["search_scope"]["transcripts"] == "omitted_by_default"
    assert 'explicitly including "transcripts" in facets' in (
        result["search_scope"]["transcript_search_hint"]
    )
    assert counts(result) == {
        "needle": {"work_items": 2, "artifacts": 1, "transcripts": None},
        "absentword": {"work_items": 0, "artifacts": 0, "transcripts": None},
    }


def test_explicit_transcript_selection_counts_sessions(api, project, mixed):
    result = search(api, project, q="needle absentword", fulltext=True,
                    facets=["work_items", "artifacts", "transcripts"])
    assert result["search_scope"]["transcripts"] == "searched"
    assert result["search_scope"]["searched_facets"] == ["work_items", "artifacts", "transcripts"]
    assert counts(result)["needle"] == {"work_items": 2, "artifacts": 1, "transcripts": 1}
    assert counts(result)["absentword"] == {"work_items": 0, "artifacts": 0, "transcripts": 0}
    response = api.post(f"/api/v1/projects/{project['id']}/transcripts/search-content",
                        json={"query": "needle absentword", "fulltext": True})
    assert response.status_code == 200, response.text
    assert counts(response.json())["needle"] == {
        "work_items": None, "artifacts": None, "transcripts": 1,
    }


def test_term_counts_follow_filters_and_metadata_opt_in(api, project, mixed):
    result = search(api, project, q="needle absentword", facets=["artifacts", "transcripts"])
    assert counts(result)["needle"] == {"work_items": None, "artifacts": 1, "transcripts": 0}
    narrowed = search(api, project, q="needle absentword", fulltext=True,
                      filters={"artifacts": {"artifact_id": str(uuid4())},
                               "work_items": {"status": "done"}})
    assert counts(narrowed)["needle"] == {"work_items": 0, "artifacts": 0, "transcripts": None}


def test_disabled_sources_have_unknown_counts_not_zero(api, project, mixed):
    api.app.state.settings.artifact_max_bytes = 0
    result = search(api, project, q="needle absentword", fulltext=True)
    assert result["search_scope"]["searched_facets"] == ["work_items"]
    assert counts(result)["needle"] == {"work_items": 2, "artifacts": None, "transcripts": None}
    assert result["indexing_incomplete"]


def test_sensitive_bodies_never_leak_through_diagnostic_counts(api, project, artifact_storage):
    upload(api, project, filename="private.txt", body=b"secretneedle",
           metadata={"sensitive": True})
    extract(api, artifact_storage)
    result = search(api, project, q="secretneedle absentword", fulltext=True)
    assert counts(result)["secretneedle"]["artifacts"] == 0
    assert result["coverage"]["artifacts"]["sensitive_content_withheld"] == 1
    dedicated = artifact_search(api, project, "secretneedle absentword", fulltext=True)
    assert counts(dedicated)["secretneedle"]["artifacts"] == 0
    assert dedicated["sensitive_content_withheld"] == 1


def test_artifact_counts_distinguish_absence_from_no_cooccurrence(api, project, artifact_storage):
    first = upload(api, project, filename="one.txt", body=b"FastAPI FastAPI")
    extract(api, artifact_storage)
    upload(api, project, filename="two.txt", body=b"AsyncSession")
    extract(api, artifact_storage)
    result = artifact_search(api, project, "FastAPI AsyncSession", fulltext=True)
    assert result["match_mode"] == "all_terms" and result["total"] == 0
    assert counts(result) == {
        "fastapi": {"work_items": None, "artifacts": 1, "transcripts": None},
        "asyncsession": {"work_items": None, "artifacts": 1, "transcripts": None},
    }
    scoped = artifact_search(api, project, "FastAPI AsyncSession", fulltext=True,
                             artifact_id=first["id"])
    assert counts(scoped)["asyncsession"]["artifacts"] == 0
    pending = upload(api, project, filename="pending.txt", body=b"FastAPI")
    unknown = artifact_search(api, project, "FastAPI AsyncSession", fulltext=True,
                              artifact_id=pending["id"])
    assert unknown["indexing"]["pending"] == 1
    assert counts(unknown)["fastapi"]["artifacts"] == 0


def test_successful_queries_and_out_of_range_pages_have_no_failure_diagnostics(api, project, mixed):
    result = search(api, project, q="needle", fulltext=True, offset=100)
    assert result["total"] > 0 and result["items"] == []
    assert result["term_diagnostics"] == []
    assert result["search_scope"]["transcripts"] == "searched"
    dedicated = artifact_search(api, project, "rare needle", fulltext=True, offset=100)
    assert dedicated["total"] == 1 and dedicated["term_diagnostics"] == []
    assert search(api, project)["term_diagnostics"] == []


def test_term_counts_preserve_source_specific_accent_matching(
    api, project, work_payload, artifact_storage,
):
    from .test_work_items_postgres import create_work

    create_work(api, project, work_payload, title="Café")
    upload(api, project, filename="accent.txt", body="Café".encode())
    extract(api, artifact_storage)
    assert search(api, project, q="Café", facets=["work_items"])["total"] == 1
    result = search(api, project, q="Café cafe absentword", fulltext=True)
    assert result["total"] == 0
    assert counts(result) == {
        "café": {"work_items": 1, "artifacts": 1, "transcripts": None},
        "cafe": {"work_items": 0, "artifacts": 1, "transcripts": None},
        "absentword": {"work_items": 0, "artifacts": 0, "transcripts": None},
    }
