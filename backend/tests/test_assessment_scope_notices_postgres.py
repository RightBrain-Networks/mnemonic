"""Disposition progress returns persistent scope notices without changing old receipts."""

from uuid import uuid4

import pytest

from .test_assessment_batch_postgres import audit_dispositions, endpoint
from .test_assessment_progress_postgres import progress_url, request, review_claim
from .test_leases_postgres import item_path
from .test_review_scope_notices_postgres import acknowledge

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("kind", ["single", "batch"])
@pytest.mark.parametrize("moved", [False, True])
def test_progress_notice_is_top_level_and_replays_after_acknowledgement(
    api, project, postgres_engine, kind, moved,
):
    assessment, candidates, owner, work, claim = review_claim(api, project, moved=moved)
    notice = api.get(item_path(owner, work), params={"status_only": True}).json()[
        "review_scope_changed"
    ]
    assert notice["review_work_item_id"] == work["id"]
    url = progress_url(project, assessment, kind)
    token_free = api.post(url, json=request(candidates, kind))
    assert token_free.status_code == 201, token_free.text
    assert "review_scope_changed" not in token_free.json()
    intent = request(candidates, kind, expected_version=1, lease_token=claim["lease_token"])
    saved = api.post(url, json=intent)
    assert saved.status_code == 201, saved.text
    assert saved.json()["review_scope_changed"] == notice
    assert all("review_scope_changed" not in item for item in saved.json().get("items", []))
    acknowledge(api, project, notice["change_id"], notice["new_scope_version"],
                claim["lease_token"])
    assert api.post(url, json=intent).json() == saved.json()
    fresh = api.post(url, json={**intent, "client_operation_id": str(uuid4()),
                               "expected_version": 2})
    assert fresh.status_code == 201, fresh.text
    assert "review_scope_changed" not in fresh.json()
    history = api.get(endpoint(project, assessment) + "/dispositions").json()
    assert all("review_scope_changed" not in item for item in history["items"])
    audit_dispositions(postgres_engine)
