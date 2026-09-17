"""Exploration and supporting work evidence use one selected-project population."""

import pytest

from .test_multi_project_search_postgres import across
from .test_multi_project_search_postgres import other as other
from .test_search_exploration_postgres import _tagged
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("query_mode", ["terms", "phrase", "literal"])
def test_project_union_keeps_tags_diagnostics_dates_and_field_scope_together(
    api, project, other, work_payload, query_mode,
):
    _tagged(api, project, work_payload, "copper zircon older", ["older"])
    first = _tagged(api, project, work_payload, "copper zircon", ["Shared", "alpha"])
    second = _tagged(api, other, work_payload, "copper zircon", ["shared", "beta"])
    _tagged(api, other, work_payload, "copper feldspar", ["delta"])
    create_work(api, other, work_payload, title="Unrelated", summary="copper zircon",
                initial_checkpoint=work_payload["initial_checkpoint"] | {"tags": ["decoy"]})
    options = {
        "q": "copper zircon", "query_mode": query_mode, "facets": ["work_items"],
        "filters": {"work_items": {"work_fields": ["title"],
                                    "created_after": first["created_at"]}},
        "tag_counts": {}, "diagnostics": "always",
    }
    result = across(api, [project, other], **options)
    assert result["total"] == 2
    assert {hit["id"] for hit in result["items"]} == {first["id"], second["id"]}
    assert result["tag_counts"]["items"] == [
        {"tag": "alpha", "count": 1}, {"tag": "beta", "count": 1},
        {"tag": "shared", "count": 2},
    ]
    counts = {row["term"]: row["matches"] for row in result["term_diagnostics"]}
    assert counts == {
        "copper": {"work_items": 3, "artifacts": None, "transcripts": None},
        "zircon": {"work_items": 2, "artifacts": None, "transcripts": None},
    }
    assert {row["project_id"]: row["facet_totals"]["work_items"]
            for row in result["project_coverage"]} == {project["id"]: 1, other["id"]: 1}
    for hit in result["items"]:
        assert hit["work_item"]["matched_fields"] == ["title"]
        assert hit["work_item"]["excerpts"][0]["matched_member_id"] == hit["id"]
    empty = across(api, [other, project], **options, limit=1, offset=50)
    assert empty["items"] == [] and empty["total"] == 2
    assert empty["tag_counts"] == result["tag_counts"]
    assert empty["term_diagnostics"] == result["term_diagnostics"]
