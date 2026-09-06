"""Real SQL title/OR lexical parity and independent external route behavior."""

from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.external_duplicate_schemas import duplicate_title_key
from mnemonic_api.models import ClientOperation, ProjectActivity, WorkEvent, WorkItemEmbedding
from mnemonic_api.services import external_duplicate_suggestions as service

from .test_duplicate_suggestions_postgres import DeterministicEmbedder, FailingEmbedder
from .test_external_duplicate_suggestions import candidate, payload

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "title",
    [
        "  ＴＲＡＣＫＥＲ visibility  ",
        "ÄBC",
        "İSTANBUL",
        "Straße",
        "Kelvin",
        "A\u00a0B",
        "A\u2003B",
        "ＡＢＣ",
        "e\u0301",
    ],
)
def test_external_exact_uses_shipped_sql_key(postgres_engine, title):
    request = payload(title=title, external_candidates=[candidate(title=title)])
    with Session(postgres_engine) as database:
        actual = database.scalar(
            text("SELECT mnemonic_duplicate_title_key_v1(:title)"), {"title": title}
        )
    assert actual == duplicate_title_key(title)
    result = service.capture_external_baseline(
        sessionmaker(postgres_engine), request, monotonic() + 2
    )
    assert result.exact_urls == (request.external_candidates[0].url,)


def test_partial_title_overlap_survives_disjoint_draft_fields_without_inference(api, project):
    api.app.state.semantic_embedder = FailingEmbedder()
    request = payload(
        title="Tracker visibility",
        summary="Zebras and orbital spectroscopy",
        initial_prompt="Marmalade recipes",
        tags=["pancakes"],
        external_candidates=[candidate(title="Tracker discovery links", body="")],
    )
    response = api.post(
        f"/api/v1/projects/{project['id']}/duplicate-suggestions",
        json=request.model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["external_scope"] == "lexical"
    assert result["external_items"][0]["signals"] == ["lexical"]
    assert result["items"] == []


def test_stopword_only_exact_and_unrelated_controls(postgres_engine):
    request = payload(
        title="the",
        summary="the",
        initial_prompt="and",
        external_candidates=[
            candidate(0, title="the", body=""),
            candidate(1, title="planetary banana", body=""),
        ],
    )
    baseline = service.capture_external_baseline(
        sessionmaker(postgres_engine), request, monotonic() + 2
    )
    assert baseline.exact_urls == (request.external_candidates[0].url,)
    assert baseline.lexical_urls == ()


def test_external_lane_no_durable_effects_and_omission_is_unchanged(api, project, postgres_engine):
    api.app.state.semantic_embedder = DeterministicEmbedder()
    path = f"/api/v1/projects/{project['id']}/duplicate-suggestions"
    request = payload()
    omitted = api.post(path, json=request.model_dump(mode="json"))
    empty = api.post(path, json={**request.model_dump(mode="json"), "external_candidates": []})
    assert omitted.status_code == empty.status_code == 200
    assert omitted.content == empty.content
    with Session(postgres_engine) as database:
        before = [
            database.scalar(select(func.count()).select_from(model))
            for model in (WorkEvent, ProjectActivity, ClientOperation, WorkItemEmbedding)
        ]
    response = api.post(
        path, json=payload(external_candidates=[candidate()]).model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["external_scope"] == "hybrid"
    assert result["external_items"][0]["reference"] == {
        key: value for key, value in candidate().items() if key != "body"
    }
    assert "body" not in result["external_items"][0]["reference"]
    assert {k: v for k, v in result.items() if not k.startswith("external_")} == omitted.json()
    with Session(postgres_engine) as database:
        after = [
            database.scalar(select(func.count()).select_from(model))
            for model in (WorkEvent, ProjectActivity, ClientOperation, WorkItemEmbedding)
        ]
    assert before == after
    missing = api.post(
        f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        json=payload(external_candidates=[candidate()]).model_dump(mode="json"),
    )
    assert missing.status_code == 404


def test_external_failure_preserves_completed_internal_hybrid_page(api, project, monkeypatch):
    api.app.state.semantic_embedder = DeterministicEmbedder()

    def fail(*_args):
        raise RuntimeError("private provider text must not appear in logs")

    monkeypatch.setattr(service, "capture_external_baseline", fail)
    response = api.post(
        f"/api/v1/projects/{project['id']}/duplicate-suggestions",
        json=payload(external_candidates=[candidate()]).model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "hybrid_full"
    assert result["external_scope"] == "unavailable"
    assert result["external_items"] == [] and result["external_candidate_count"] == 1


def test_external_pool_checkout_uses_remaining_budget_and_releases_worker(postgres_engine):
    import asyncio

    from sqlalchemy import create_engine

    from mnemonic_api.application.suggestion_resources import OwnedSuggestionWork
    from mnemonic_api.pool_deadlines import DeadlineQueuePool

    engine = create_engine(
        postgres_engine.url,
        poolclass=DeadlineQueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.8,
    )
    request = payload(external_candidates=[candidate()])

    async def exercise():
        owner = OwnedSuggestionWork()
        started = monotonic()
        baseline = await service._external_baseline(
            request, sessionmaker(engine), started + 0.05, owner
        )
        assert baseline is None
        # The actual worker exits at its checkout budget; retention cannot extend
        # to the pool's normal 800ms wait when no model work exists.
        await asyncio.wait_for(owner.finish(), timeout=0.2)
        assert not owner.pending and monotonic() - started < 0.3

    try:
        with engine.connect():
            asyncio.run(exercise())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
    finally:
        engine.dispose()
