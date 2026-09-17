"""Exact intent and exploration share the same bounded, filtered search corpus."""

import pytest

from mnemonic_api.artifact_index import ArtifactSearchIndex

from .test_artifact_search_postgres import extract
from .test_artifact_search_postgres import search as artifact_search
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_search_diagnostics_postgres import counts
from .test_search_exploration_postgres import _work_page
from .test_transcript_query_intent_postgres import capture
from .test_transcript_query_intent_postgres import query as transcript_search
from .test_transcript_search_postgres import seed_transcripts
from .test_unified_search_postgres import search
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["terms", "phrase", "literal"])
def test_work_positive_diagnostics_obey_fields_dates_and_exact_tag_population(
    api, project, work_payload, mode,
):
    create_work(api, project, work_payload, title="copper zircon")
    first = create_work(api, project, work_payload, title="copper zircon",
        initial_checkpoint=work_payload["initial_checkpoint"] | {"tags": ["matching"]})["work_item"]
    create_work(api, project, work_payload, title="ordinary task", summary="copper zircon")
    options = {"work_fields": ["title"], "created_after": first["created_at"]}
    result = search(api, project, q="copper zircon", query_mode=mode, facets=["work_items"],
                    diagnostics="always", filters={"work_items": options}, tag_counts={})
    dedicated = _work_page(api, project, q="copper zircon", query_mode=mode,
                            diagnostics="always", **options)
    assert result["total"] == dedicated["total"] == 1
    assert counts(result) == counts(dedicated) == {
        word: {"work_items": 1, "artifacts": None, "transcripts": None}
        for word in ("copper", "zircon")
    }
    assert result["tag_counts"]["items"] == [{"tag": "matching", "count": 1}]


@pytest.mark.parametrize("facet", ["artifacts", "transcripts"])
def test_literal_diagnostics_off_avoids_building_a_count_only_index(
    api, project, artifact_storage, monkeypatch, facet,
):
    if facet == "artifacts":
        upload(api, project, filename="record.txt", body=b"unrelated body")
        extract(api, artifact_storage)
    else:
        seed_transcripts(api, project, "unrelated legacy body")

    def forbidden(*_args, **_kwargs):
        pytest.fail("literal diagnostics=off must not build a count-only index")

    monkeypatch.setattr(ArtifactSearchIndex, "_build", forbidden)
    options = {"query_mode": "literal", "fulltext": True, "diagnostics": "off"}
    result = search(api, project, q="absent exact text", facets=[facet], **options)
    assert result["total"] == 0 and result["term_diagnostics"] == []
    dedicated = (artifact_search(api, project, "absent exact text", **options)
                 if facet == "artifacts" else
                 transcript_search(api, project, "absent exact text", **options))
    assert dedicated["total"] == 0 and dedicated["term_diagnostics"] == []


@pytest.mark.parametrize("endpoint", ["content", "unified"])
def test_literal_positive_transcript_diagnostics_use_current_kind_and_date_scope(
    api, project, work_payload, tmp_path, postgres_engine, endpoint,
):
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    capture(api, project, work_payload, first_dir, postgres_engine, ["copper zircon"])
    latest = capture(api, project, work_payload, second_dir, postgres_engine, ["copper zircon"])
    filters = {"content_kinds": ["human_text"], "created_after": latest["created_at"]}
    options = {"filters": {"transcripts": filters}} if endpoint == "unified" else filters
    result = transcript_search(api, project, "copper zircon", query_mode="literal",
                                endpoint=endpoint, diagnostics="always", **options)
    assert result["total"] == 1 and result["items"][0]["id"] == latest["id"]
    assert counts(result) == {
        word: {"work_items": None, "artifacts": None, "transcripts": 1}
        for word in ("copper", "zircon")
    }


@pytest.mark.parametrize("endpoint", ["content", "unified"])
def test_literal_positive_artifact_diagnostics_preserve_date_scope_and_sensitive_omission(
    api, project, artifact_storage, endpoint,
):
    upload(api, project, filename="earlier.txt", body=b"copper zircon")
    extract(api, artifact_storage)
    latest = upload(api, project, filename="latest.txt", body=b"copper zircon")
    extract(api, artifact_storage)
    upload(api, project, filename="private.txt", body=b"copper zircon",
           metadata={"sensitive": True})
    extract(api, artifact_storage)
    filters = {"created_after": latest["created_at"]}
    options = {"query_mode": "literal", "fulltext": True, "diagnostics": "always"}
    result = (artifact_search(api, project, "copper zircon", **options, **filters)
              if endpoint == "content" else search(api, project, q="copper zircon",
                  facets=["artifacts"], filters={"artifacts": filters}, **options))
    assert result["total"] == 1
    assert counts(result) == {
        word: {"work_items": None, "artifacts": 1, "transcripts": None}
        for word in ("copper", "zircon")
    }
