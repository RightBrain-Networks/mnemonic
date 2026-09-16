"""Every search result explains its actual scope, including filtered empty pages."""

from uuid import uuid4

import pytest

from .report_fixtures import reported
from .test_artifact_search_postgres import search as artifact_search
from .test_semantic_postgres import DeterministicEmbedder
from .test_transcript_search_postgres import search as transcript_search
from .test_unified_search_postgres import search
from .test_work_items_postgres import checkpoint_payload, collection, create_work, item_path

pytestmark = pytest.mark.postgres


def work_search(api, project, **params):
    response = api.get(collection(project), params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("view", ["full", "roots"])
def test_work_discovery_defaults_to_all_statuses_and_explicit_pending_is_disclosed(
    api, project, work_payload, view,
):
    completed = create_work(api, project, work_payload, title="Historical cookie admission")
    deferred = create_work(api, project, work_payload, title="Deferred cookie admission")
    completion = api.post(item_path(project, completed["work_item"]) + "/complete", json=reported({
        "expected_version": 1,
        "checkpoint": {key: value for key, value in checkpoint_payload(
            "Cookie admission implemented and verified", "historical-session",
        ).items() if key != "kind"},
    }))
    assert completion.status_code == 200, completion.text
    deferral = api.post(item_path(project, deferred["work_item"]) + "/defer", json={
        "expected_version": 1,
        "actor": {"actor_client": "dashboard", "actor_session_id": "deferral-session"},
    })
    assert deferral.status_code == 200, deferral.text
    query = {"q": "cookie admission"} if view == "full" else {}
    page = work_search(api, project, view=view, **query)
    assert page["total"] == 2
    assert page["applied_filters"]["work_items"]["status"] == "all"
    assert page["applied_filters"]["work_items"]["view"] == view
    assert page["applied_filters"]["project_id"] == project["id"]
    assert page["applied_filters"]["artifacts"] is None
    assert page["applied_filters"]["transcripts"] is None
    assert page["query_interpretation"]["work_items"]["match_mode"] == (
        "postgresql_plain_terms_or_substring" if query else "browse"
    )
    pending = work_search(api, project, view=view, status="pending", **query)
    assert pending["total"] == 0 and pending["items"] == []
    assert pending["applied_filters"]["work_items"]["status"] == "pending"
    assert api.get(f"/api/v1/projects/{project['id']}/ready-work").json()["total"] == 0
    assert search(api, project, **query)["facet_totals"]["work_items"] == 2


@pytest.mark.parametrize("offset", [0, 50])
def test_work_filters_and_quoted_query_are_disclosed_on_every_page(
    api, project, work_payload, offset,
):
    create_work(api, project, work_payload, title="Cookie admission")
    page = work_search(api, project, q=' "cookie admission" ', tag="CACHE", offset=offset)
    assert page["total"] == 1
    assert bool(page["items"]) == (offset == 0)
    assert page["applied_filters"]["work_items"]["tag"] == "cache"
    interpretation = page["query_interpretation"]
    assert interpretation["q"] == '"cookie admission"'
    assert interpretation["work_items"] == {
        "match_mode": "postgresql_phrase_terms", "fulltext": None,
        "fields": ["title", "summary", "tags", "checkpoint", "identifiers", "provenance"],
    }
    assert interpretation["artifacts"] is None and interpretation["transcripts"] is None
    assert page["warnings"] == []


def test_unified_empty_result_echoes_normalized_effective_filters(api, project):
    artifact_id, work_id = str(uuid4()), str(uuid4())
    page = search(api, project, q='"cookie admission"', fulltext=True, filters={
        "work_items": {"status": "done", "tag": "DISCOVERY", "source_client": "codex",
                       "source_session_id": "prior-session", "duplicate_scope": "aliases",
                       "external_url": "https://example.com/issues/42"},
        "artifacts": {"artifact_id": artifact_id, "work_item_id": work_id,
                      "include_deleted": True, "sensitive": True, "mime_type": "text/plain",
                      "created_by_agent_session_id": "artifact-session"},
        "transcripts": {"client": "codex"},
    })
    assert page["total"] == 0
    assert page["applied_filters"] == {
        "project_id": project["id"],
        "work_items": {"status": "done", "tag": "discovery", "source_client": "codex",
                       "source_session_id": "prior-session", "duplicate_scope": "aliases",
                       "external_url": "https://example.com/issues/42",
                       "canonical_work_item_id": None, "view": "full",
                       "work_fields": ["title", "summary", "tags", "checkpoint",
                                       "identifiers", "provenance"]},
        "artifacts": {"artifact_id": artifact_id, "work_item_id": work_id,
                      "include_deleted": True, "sensitive": True, "mime_type": "text/plain",
                      "created_by_agent_session_id": "artifact-session"},
        "transcripts": None,
    }
    assert page["query_interpretation"]["transcripts"] is None
    assert page["query_interpretation"]["artifacts"] == {
        "match_mode": "phrase", "fields": ["metadata", "content"], "fulltext": True,
    }
    assert page["warnings"] == []
    assert page["search_scope"]["transcripts"] == "omitted_by_default"
    assert all(term["matches"]["transcripts"] is None for term in page["term_diagnostics"])


def test_disabled_artifacts_are_unsearched_in_disclosures(api, project):
    api.app.state.settings.artifact_max_bytes = 0
    page = search(api, project, q='"cookie admission"')
    assert page["applied_filters"]["artifacts"] is None
    assert page["query_interpretation"]["artifacts"] is None
    assert page["warnings"] == []
    assert page["coverage"]["artifacts"]["enabled"] is False


@pytest.mark.parametrize("fulltext", [False, True])
def test_dedicated_artifact_search_explains_empty_results(api, project, fulltext):
    artifact_id, work_id = str(uuid4()), str(uuid4())
    page = artifact_search(api, project, '"cookie admission"', fulltext=fulltext,
                           artifact_id=artifact_id, work_item_id=work_id, include_deleted=True)
    assert page["total"] == 0
    filters = page["applied_filters"]
    assert filters["work_items"] is None and filters["transcripts"] is None
    assert filters["artifacts"] == {
        "artifact_id": artifact_id, "work_item_id": work_id, "include_deleted": True,
        "sensitive": None, "mime_type": None, "created_by_agent_session_id": None,
    }
    assert page["query_interpretation"]["artifacts"] == {
        "match_mode": "phrase", "fulltext": fulltext,
        "fields": ["metadata", "content"] if fulltext else ["metadata"],
    }
    assert page["warnings"] == []


@pytest.mark.parametrize("endpoint", ["list", "content", "unified"])
@pytest.mark.parametrize("query", ['', '"cookie admission"'])
def test_transcript_search_and_browse_explain_their_scope(api, project, endpoint, query):
    work_id = str(uuid4())
    filters = {"work_item_id": work_id}
    if endpoint == "unified":
        filters = {"filters": {"transcripts": {"work_item_id": work_id}}}
    response = transcript_search(api, project, endpoint, query=query, fulltext=True, **filters)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 0
    assert page["applied_filters"]["project_id"] == project["id"]
    assert page["applied_filters"]["transcripts"] == {
        "work_item_id": work_id, "agent_session_id": None, "client": None,
        "kind": None, "status": None, "content_kinds": None,
    }
    assert page["query_interpretation"]["transcripts"]["match_mode"] == (
        "phrase" if query else "browse"
    )
    assert page["query_interpretation"]["work_items"] is None
    assert page["query_interpretation"]["artifacts"] is None
    assert page["warnings"] == []


def test_semantic_discovery_reports_hybrid_interpretation(api, project, work_payload):
    create_work(api, project, work_payload)
    api.app.state.semantic_embedder = DeterministicEmbedder()
    pages = [
        work_search(api, project, q="cookie admission", semantic=True),
        search(api, project, q="cookie admission", filters={"work_items": {"semantic": True}}),
    ]
    for page in pages:
        assert page["total"] == 1
        assert page["query_interpretation"]["work_items"]["match_mode"] == "hybrid_lexical_semantic"
        assert page["warnings"] == []
