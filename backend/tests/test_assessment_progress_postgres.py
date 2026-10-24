"""Assessment progress renews only a matching current implementation review driver."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from mnemonic_api.impact_schemas import ImpactDispositionBatchCreate
from mnemonic_api.services import decision_change_locks

from .test_assessment_batch_postgres import ACTOR, batch_body, endpoint, episode
from .test_decision_change_locking_postgres import _move
from .test_decision_changes_postgres import update
from .test_decision_reviews_postgres import context, reviews
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path
from .test_progress_lease_postgres import renewals, retained
from .test_variable_leases_postgres import change_policy
from .test_work_item_moves_postgres import _project

pytestmark = pytest.mark.postgres


def request(candidates, kind, **changes):
    body = batch_body(candidates, **changes)
    if kind == "single":
        first, = body.pop("items")
        body.update(first)
    return body


def progress_url(project, assessment, kind):
    return endpoint(project, assessment) + "/dispositions" + ("/batch" if kind == "batch" else "")


def review_claim(api, project, *, moved=False):
    assessment, candidates = episode(api, project, 1, automatic=True)
    driver, = reviews(api, project)
    work = context(api, project, driver["work_item_id"])["work_item"]
    owner = _project(api, "Moved review owner") if moved else project
    if moved:
        work = _move(api, project, owner, work)
    result = api.post(item_path(owner, work) + "/claim", json={
        **claim_payload(str(uuid4()), client=ACTOR["actor_client"],
                        session=ACTOR["actor_session_id"]),
        "lease_minutes": 120,
    })
    assert result.status_code == 200, result.text
    return assessment, candidates, owner, work, result.json()


@pytest.mark.parametrize("kind", ["single", "batch"])
@pytest.mark.parametrize("moved", [False, True])
def test_fresh_assessment_progress_renews_once_and_replays_after_expiry_and_takeover(
    api, project, postgres_engine, kind, moved,
):
    assessment, candidates, owner, work, claim = review_claim(api, project, moved=moved)
    before = retained(postgres_engine, work["id"])
    url = progress_url(project, assessment, kind)
    token_free = request(candidates, kind)
    assert api.post(url, json=token_free).status_code == 201
    assert retained(postgres_engine, work["id"]) == before
    assert change_policy(api, owner, lease_maximum_minutes=60).status_code == 200
    body = request(candidates, kind, expected_version=1, lease_token=claim["lease_token"])
    saved = api.post(url, json=body)
    assert saved.status_code == 201, saved.text
    after = retained(postgres_engine, work["id"])
    assert after["renewed_at"] > before["renewed_at"]
    assert (after["expires_at"] - after["renewed_at"]).total_seconds() == 3600
    assert renewals(postgres_engine, work["id"]) == 1
    assert claim["lease_token"] not in saved.text
    assert api.post(url, json=body).json() == saved.json()
    assert retained(postgres_engine, work["id"]) == after
    expire_lease(postgres_engine, work["id"])
    expired = retained(postgres_engine, work["id"])
    assert api.post(url, json=body).json() == saved.json()
    assert retained(postgres_engine, work["id"]) == expired
    fresh = {**body, "client_operation_id": str(uuid4()), "expected_version": 2}
    refused = api.post(url, json=fresh)
    assert refused.status_code == 409 and "lease_expired" in refused.text
    replacement = api.post(item_path(owner, work) + "/claim", json=claim_payload(str(uuid4())))
    assert replacement.status_code == 200
    taken = retained(postgres_engine, work["id"])
    assert api.post(url, json=body).json() == saved.json()
    refused = api.post(url, json=fresh)
    assert refused.status_code == 409 and "lease_token_mismatch" in refused.text
    assert retained(postgres_engine, work["id"]) == taken


@pytest.mark.parametrize("kind", ["single", "batch"])
@pytest.mark.parametrize("invalid", ["version", "candidate", "reason-secret", "actor-secret"])
def test_invalid_progress_rolls_back_renewal_and_never_persists_token(
    api, project, postgres_engine, kind, invalid,
):
    assessment, candidates, _, work, claim = review_claim(api, project)
    body = request(candidates, kind, lease_token=claim["lease_token"])
    item = body if kind == "single" else body["items"][0]
    if invalid == "version":
        body["expected_version"] = 5
    elif invalid == "candidate":
        item["candidate_reference"] = "ADR-99999-0"
    elif invalid == "reason-secret":
        item["reason"] = "Credential embedded: " + claim["lease_token"]
    else:
        body["actor"] = {**ACTOR, "actor_session_id": "Credential: " + claim["lease_token"]}
    before = retained(postgres_engine, work["id"])
    result = api.post(progress_url(project, assessment, kind), json=body)
    assert result.status_code in {409, 422}, result.text
    assert claim["lease_token"] not in result.text
    assert retained(postgres_engine, work["id"]) == before
    assert renewals(postgres_engine, work["id"]) == 0
    assert api.get(endpoint(project, assessment)).json()["version"] == 0


def test_batch_invalid_later_candidate_rolls_back_earlier_rows_and_renewal(
    api, project, postgres_engine,
):
    assessment, candidates, _, work, claim = review_claim(api, project)
    body = batch_body(candidates, lease_token=claim["lease_token"])
    body["items"].append({**body["items"][0], "candidate_reference": "ADR-99999-0"})
    before = retained(postgres_engine, work["id"])
    result = api.post(progress_url(project, assessment, "batch"), json=body)
    assert result.status_code == 422, result.text
    assert retained(postgres_engine, work["id"]) == before
    assert api.get(endpoint(project, assessment)).json()["version"] == 0


@pytest.mark.parametrize("kind", ["single", "batch"])
def test_unrelated_capability_cannot_renew_or_record_disposition(
    api, project, work_payload, postgres_engine, kind,
):
    assessment, candidates, _, work, _ = review_claim(api, project)
    unrelated = create_work(api, project, work_payload)["work_item"]
    claim = api.post(item_path(project, unrelated) + "/claim",
                     json=claim_payload(str(uuid4()))).json()
    before = retained(postgres_engine, unrelated["id"])
    body = request(candidates, kind, lease_token=claim["lease_token"])
    result = api.post(progress_url(project, assessment, kind), json=body)
    assert result.status_code == 409 and "lease_token_mismatch" in result.text
    assert retained(postgres_engine, unrelated["id"]) == before
    assert renewals(postgres_engine, work["id"]) == 0


def test_adopted_driver_token_selects_its_association_and_scope_tracks_moved_projects(
    api, project, work_payload, postgres_engine,
):
    assessment, candidates, _, native, _ = review_claim(api, project)
    adopted = create_work(api, project, work_payload)["work_item"]
    result, _ = update(api, project, "adopt", work_item_id=adopted["id"],
                       expected_work_version=adopted["version"], assessment_ids=[assessment["id"]])
    assert result.status_code == 200, result.text
    owner = _project(api, "Combined investigation destination")
    adopted = _move(api, project, owner, adopted)
    claim = api.post(item_path(owner, adopted) + "/claim", json=claim_payload(str(uuid4()))).json()
    native_before = retained(postgres_engine, native["id"])
    adopted_before = retained(postgres_engine, adopted["id"])
    body = batch_body(candidates, lease_token=claim["lease_token"])
    with Session(postgres_engine) as database:
        scope = decision_change_locks.discover_scope(database, "assess_decision_impact_batch",
            {"assessment_id": UUID(assessment["id"])}, ImpactDispositionBatchCreate(**body))
        assert scope.project_ids == frozenset({UUID(project["id"]), UUID(owner["id"])})
    saved = api.post(progress_url(project, assessment, "batch"), json=body)
    assert saved.status_code == 201, saved.text
    assert retained(postgres_engine, native["id"]) == native_before
    assert retained(postgres_engine, adopted["id"])["renewed_at"] > adopted_before["renewed_at"]


def test_driver_move_during_scope_discovery_requires_retry_before_any_write(
    api, project, postgres_engine, monkeypatch,
):
    assessment, candidates = episode(api, project, 1, automatic=True)
    driver, = reviews(api, project)
    work = context(api, project, driver["work_item_id"])["work_item"]
    destination = _project(api, "Moving driver")
    body = batch_body(candidates)
    original = decision_change_locks.discover_scope
    discovered, release = Event(), Event()

    def pause(database, kind, target, payload):
        scope = original(database, kind, target, payload)
        if kind == "assess_decision_impact_batch" and not discovered.is_set():
            discovered.set()
            assert release.wait(10)
        return scope

    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(decision_change_locks, "discover_scope", pause)
        future = pool.submit(api.post, progress_url(project, assessment, "batch"), json=body)
        assert discovered.wait(10)
        try:
            _move(api, project, destination, work)
        finally:
            release.set()
        response = future.result(10)
    assert response.status_code == 503 and "client_operation_unavailable" in response.text
    assert api.get(endpoint(project, assessment)).json()["version"] == 0
    retry = api.post(progress_url(project, assessment, "batch"), json=body)
    assert retry.status_code == 201, retry.text


@pytest.mark.parametrize("kind", ["single", "batch"])
def test_associated_code_review_lease_cannot_renew_or_append(
    api, project, checkpoint_fields, postgres_engine, kind,
):
    from .code_review_fixtures import handoff
    from .test_decision_reviews_postgres import finish
    from .test_manual_review_requests_postgres import request_review

    assessment, candidates = episode(api, project, 1, automatic=True)
    driver, = reviews(api, project)
    work = context(api, project, driver["work_item_id"])["work_item"]
    url = progress_url(project, assessment, kind)
    assert api.post(url, json=request(candidates, kind)).status_code == 201
    completed = finish(api, project, work, checkpoint_fields)
    assert completed.status_code == 200, completed.text
    work = completed.json()["work_item"]
    requested, _ = request_review(api, project, work)
    assert requested.status_code == 200, requested.text
    review = context(api, project, work["id"])["code_review_context"]["current_review"]
    response = api.post(item_path(project, work) + "/claim", json={
        **claim_payload(str(uuid4())), "purpose": "code_review", "mode": "warm",
        "code_review_id": review["id"], "code_review_handoff": handoff(),
    })
    assert response.status_code == 200, response.text
    before = retained(postgres_engine, work["id"])
    body = request(candidates, kind, expected_version=1, lease_token=response.json()["lease_token"])
    refused = api.post(url, json=body)
    assert refused.status_code == 409 and "lease_purpose_mismatch" in refused.text
    assert retained(postgres_engine, work["id"]) == before
    assert api.get(endpoint(project, assessment)).json()["version"] == 1
