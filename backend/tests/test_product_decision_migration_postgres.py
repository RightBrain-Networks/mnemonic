"""Product decisions preserve old questions and reject altered restored history."""

import hashlib
import io
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, defer
from sqlalchemy.orm.attributes import set_committed_value

from mnemonic_api.knowledge_integrity import KNOWLEDGE_FINDINGS
from mnemonic_api.knowledge_schemas import KnowledgeDomainRead
from mnemonic_api.models import ProjectSettings, WorkItem
from mnemonic_api.product_decision_content import product_decision_content
from mnemonic_api.services import knowledge, product_decisions
from mnemonic_backup.archive import BackupError, restore_project

from .conftest import BACKEND_DIR
from .test_human_gates_postgres import (
    create_work,
    gate_path,
    gate_request,
    resolution_payload,
)
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_question_versions_postgres import revise
from .test_work_item_moves_postgres import _move_payload

pytestmark = pytest.mark.postgres


def migrate(engine, target, *, downgrade=False):
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, target)


@contextmanager
def _legacy_behavior(monkeypatch):
    # Exercise the existing 0055 gate protocol without calling the new document
    # service against tables that deliberately do not exist in the old schema.
    for name in ("require_question_author", "write_question_decision", "write_answer_decision"):
        monkeypatch.setattr(product_decisions, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(product_decisions, "gate_decision_read", lambda *args: None)
    monkeypatch.setattr(product_decisions, "decision_gate_read", lambda *args: None)
    monkeypatch.setattr(knowledge, "require_no_knowledge_links", lambda *args: None)
    monkeypatch.setattr(knowledge, "domain_read", lambda database, domain: KnowledgeDomainRead(
        **{key: getattr(domain, key) for key in (
            "id", "project_id", "name", "prefix", "version", "created_at", "updated_at",
        )}, decision_count=0,
    ))

    with _historical_automatic_review_columns():
        yield


@contextmanager
def _historical_automatic_review_columns():
    missing = {
        WorkItem: {"decision_review_revision_id": None, "decision_review_change_id": None,
                   "decision_review_project_id": None, "decision_change_ids": [],
                   "decision_change_projects": {}},
        ProjectSettings: {"automatic_decision_reviews": False, "decision_review_priority": 40},
    }

    def historical_columns(state):
        if not state.is_select:
            return
        for model, fields in missing.items():
            if any(item.get("expr") is model for item in state.statement.column_descriptions):
                state.statement = state.statement.options(
                    *(defer(getattr(model, name)) for name in fields)
                )

    def historical_defaults(record, *_args):
        for name, value in missing[type(record)].items():
            set_committed_value(record, name, value)

    event.listen(Session, "do_orm_execute", historical_columns)
    for model in missing:
        event.listen(model, "load", historical_defaults)
        event.listen(model, "refresh", historical_defaults)
    try:
        with patch("mnemonic_api.services.decision_reviews.require_decision_impact_review_complete",
                   lambda *_: None), patch(
                       "mnemonic_api.services.decision_change_locks.discover_scope",
                       lambda *_: None,
                   ):
            yield
    finally:
        for model in missing:
            event.remove(model, "load", historical_defaults)
            event.remove(model, "refresh", historical_defaults)
        event.remove(Session, "do_orm_execute", historical_columns)


def _knowledge_findings(engine):
    with engine.connect() as connection:
        return {key: count for key, query in KNOWLEDGE_FINDINGS.items()
                if (count := connection.scalar(text(query)))}


def legacy_resolved_moved_gate(api, project, work_payload, postgres_engine, monkeypatch):
    """Create a real pre-PDR question, answer and witnessed cross-project move."""
    work = create_work(api, project, work_payload, title="A product decision before relocation")
    target = api.post("/api/v1/projects", json={"name": "Historical work destination"})
    assert target.status_code == 201, target.text
    target = target.json()
    path = gate_path(project, work)
    migrate(postgres_engine, "0055_decision_provenance", downgrade=True)
    with monkeypatch.context() as legacy, _legacy_behavior(legacy):
        requested = api.post(path, json=gate_request(operation_id=uuid4(),
            question="Should this relocated feature retain the original owner decision?"))
        assert requested.status_code == 201, requested.text
        gate = requested.json()
        answered = api.post(f"{path}/{gate['id']}/resolve", json=resolution_payload(
            revision=gate["current_context_revision"], operation_id=uuid4(),
        ))
        assert answered.status_code == 200, answered.text
        moved = api.post(
            f"/api/v1/projects/{project['id']}/work-items/{work['work_item']['id']}/move",
            json=_move_payload(target, work["work_item"]["version"]),
        )
        assert moved.status_code == 200, moved.text
    migrate(postgres_engine, "head")
    return work["work_item"], answered.json(), target


def test_backfill_preserves_resolved_question_when_its_work_previously_moved(
    api, project, work_payload, postgres_engine, monkeypatch,
):
    work, gate, target = legacy_resolved_moved_gate(
        api, project, work_payload, postgres_engine, monkeypatch,
    )
    decisions_path = f"/api/v1/projects/{project['id']}/knowledge/decisions"
    document = api.get(decisions_path).json()["items"][0]
    assert document["project_id"] == project["id"]
    assert document["human_gate"]["work_item_id"] == work["id"]
    assert document["human_gate"]["work_project_id"] == target["id"]
    assert document["related_work_item_ids"] == []
    text_response = api.get(f"{decisions_path}/{document['decision_id']}")
    assert text_response.status_code == 200, text_response.text
    assert text_response.json()["content"] == product_decision_content(
        gate["question"], gate["resolution"],
    )
    history = api.get(f"{decisions_path}/{document['decision_id']}/history")
    assert history.status_code == 200, history.text
    assert [(row["revision"], row["state"]) for row in history.json()["items"]] == [
        (1, "accepted"), (0, "proposed"),
    ]
    search = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "relocated feature", "facets": ["decisions"], "fulltext": True,
    })
    assert search.status_code == 200, search.text
    assert search.json()["total"] == 1
    gates = api.get(f"/api/v1/projects/{target['id']}/work-items/{work['id']}/gates")
    assert gates.status_code == 200, gates.text
    retained = gates.json()["items"][0]
    assert retained["project_id"] == project["id"]
    assert retained["decision"]["originating_work_project_id"] == target["id"]
    assert retained["decision"]["reference"] == document["reference"]
    assert _knowledge_findings(postgres_engine) == {}
    # The source project archive retains the PDR and gate; the global work
    # identity and its move history remain owned by the destination project.
    before = _snapshot(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]),
                    io.BytesIO(_export(postgres_engine, project)))
    after = _snapshot(postgres_engine, project)
    for table in ("product_decision_requests", "work_gates", "decision_documents",
                  "decision_revisions", "decision_work_item_links"):
        assert after[table] == before[table]


def test_backfill_retains_every_question_answer_actor_and_existing_pdr_ordinal(
    api, project, work_payload, postgres_engine, monkeypatch,
):
    work = create_work(api, project, work_payload, title="Choose the release window")
    path = gate_path(project, work)
    migrate(postgres_engine, "0055_decision_provenance", downgrade=True)
    with monkeypatch.context() as legacy, _legacy_behavior(legacy):
        response = api.post(f"/api/v1/projects/{project['id']}/knowledge/domains", json={
            "name": "Existing owner policies", "prefix": "PDR",
            "client_operation_id": str(uuid4()),
        })
        assert response.status_code == 201, response.text
        domain = response.json()
        with postgres_engine.begin() as connection:
            # No document renumbering: the allocator can already have gaps from a
            # preserved-number import, just as ADR/BDR imports do in production.
            connection.execute(text("ALTER TABLE knowledge_domains DISABLE TRIGGER "
                                    "knowledge_domain_guard"))
            connection.execute(text("UPDATE knowledge_domains SET next_ordinal=17 WHERE id=:id"),
                               {"id": domain["id"]})
            connection.execute(text("ALTER TABLE knowledge_domains ENABLE TRIGGER "
                                    "knowledge_domain_guard"))
        original_payload = gate_request(question="  Original café question.\r\n")
        original_payload["requested_by_model"] = None
        original = api.post(path, json=original_payload)
        assert original.status_code == 201, original.text
        current, _ = revise(api, path, original.json(), "Revised question: Tuesday?\r\n")
        assert current.status_code == 201, current.text
        answer = resolution_payload(revision=current.json()["current_context_revision"],
                                    operation_id=uuid4())
        answer["expected_question_version"] = 2
        answered = api.post(f"{path}/{original.json()['id']}/resolve", json=answer)
        assert answered.status_code == 200, answered.text
        duplicate = api.post(path, json=gate_request(question=original_payload["question"]))
        assert duplicate.status_code == 201, duplicate.text
        with postgres_engine.connect() as connection:
            before = list(connection.execute(text(
                "SELECT row_to_json(gate) FROM work_gates gate ORDER BY attention_sequence"
            )).scalars())
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        after = list(connection.execute(text(
            "SELECT row_to_json(gate) FROM work_gates gate ORDER BY attention_sequence"
        )).scalars())
        assert after == before
        binding = connection.execute(text(
            "SELECT * FROM project_product_decision_domains WHERE project_id=:id"
        ), {"id": project["id"]}).mappings().one()
        assert str(binding["domain_id"]) == domain["id"]
        assert binding["created_automatically"] is False
        assert connection.scalar(text("SELECT name FROM knowledge_domains WHERE id=:id"),
                                 {"id": domain["id"]}) == domain["name"]
        documents = list(connection.execute(text(
            "SELECT ordinal,current_revision FROM decision_documents ORDER BY ordinal"
        )))
        assert documents == [(17, 2), (18, 0)]
        revisions = list(connection.execute(text("""
            SELECT revision.*,document.ordinal FROM decision_revisions revision
            JOIN decision_documents document ON document.id=revision.document_id
            ORDER BY ordinal,revision
        """)).mappings())
        assert [row["content"] for row in revisions] == [
            original_payload["question"], current.json()["question"],
            product_decision_content(current.json()["question"], answer["resolution"]),
            original_payload["question"],
        ]
        assert [row["state"] for row in revisions] == [
            "proposed", "proposed", "accepted", "proposed",
        ]
        assert revisions[0]["model_id"] is None
        assert revisions[0]["author_kind"] == "agent"
        assert revisions[0]["agent_session_id"] == original_payload["requested_by_session_id"]
        assert revisions[1]["model_id"] == "test-model"
        assert revisions[2]["author_kind"] == "human"
        assert revisions[2]["agent_session_id"] is revisions[2]["model_id"] is None
        assert revisions[0]["created_at"] == datetime.fromisoformat(before[0]["created_at"])
        assert connection.scalar(text("SELECT count(*) FROM decision_impact_assessments")) == 0
    assert _knowledge_findings(postgres_engine) == {}
    fresh, _ = revise(api, path, duplicate.json(), "A new question after the migration")
    assert fresh.status_code == 201, fresh.text
    assert _knowledge_findings(postgres_engine) == {}
    archive = _export(postgres_engine, project)

    def discard_fresh_receipt(_header, rows):
        rows["client_operations"] = [receipt for receipt in rows["client_operations"]
            if receipt["response_body"].get("decision", {}).get("reference")
                != fresh.json()["decision"]["reference"]]

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]),
                        io.BytesIO(_rewrite(archive, discard_fresh_receipt)))
    with pytest.raises(RuntimeError, match="Decision change identities"):
        migrate(postgres_engine, "0055_decision_provenance", downgrade=True)


def test_default_domain_avoids_name_collision_and_empty_downgrade_keeps_adopted_domain(
    api, project, postgres_engine, monkeypatch,
):
    migrate(postgres_engine, "0055_decision_provenance", downgrade=True)
    with monkeypatch.context() as legacy, _legacy_behavior(legacy):
        domain = api.post(f"/api/v1/projects/{project['id']}/knowledge/domains", json={
            "name": "Product Decision Records", "prefix": "OLD",
            "client_operation_id": str(uuid4()),
        })
    assert domain.status_code == 201, domain.text
    migrate(postgres_engine, "head")
    domains = api.get(f"/api/v1/projects/{project['id']}/knowledge/domains").json()["items"]
    assert {(item["prefix"], item["name"]) for item in domains} == {
        ("OLD", "Product Decision Records"), ("PDR", "Product Decision Records (PDR)"),
    }
    assert _knowledge_findings(postgres_engine) == {}
    migrate(postgres_engine, "0055_decision_provenance", downgrade=True)
    with postgres_engine.connect() as connection:
        assert list(connection.scalars(text("SELECT prefix FROM knowledge_domains"))) == ["OLD"]
    migrate(postgres_engine, "head")


@pytest.mark.parametrize("forgery", [
    "content", "title", "state", "author", "missing_link", "missing_receipt",
    "missing_resolution_receipt",
])
def test_product_decision_restore_rejects_rewritten_source_or_metadata(
    api, project, work_payload, postgres_engine, forgery,
):
    work = create_work(api, project, work_payload, title="A retained product question")
    response = api.post(gate_path(project, work), json=gate_request(operation_id=uuid4()))
    assert response.status_code == 201, response.text
    if forgery == "missing_resolution_receipt":
        gate = response.json()
        response = api.post(f"{gate_path(project, work)}/{gate['id']}/resolve", json=
                            resolution_payload(revision=gate["current_context_revision"],
                                               operation_id=uuid4()))
        assert response.status_code == 200, response.text
    before, archive = _snapshot(postgres_engine, project), _export(postgres_engine, project)

    def forge(_header, rows):
        revision = rows["decision_revisions"][0]
        if forgery == "content":
            revision["content"] = "A different question"
            revision["size_bytes"] = len(revision["content"].encode())
            revision["sha256"] = hashlib.sha256(revision["content"].encode()).hexdigest()
        elif forgery == "title":
            revision["title"] = "An altered metadata title"
        elif forgery == "state":
            revision["state"] = "accepted"
        elif forgery == "author":
            revision["author_kind"] = "human"
            revision["agent_session_id"] = revision["model_id"] = None
        elif forgery == "missing_link":
            rows["decision_work_item_links"] = []
        else:
            kind = ("resolve_human_input" if forgery == "missing_resolution_receipt"
                    else "request_human_input")
            rows["client_operations"] = [receipt for receipt in rows["client_operations"]
                                         if receipt["operation_kind"] != kind]

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    assert _snapshot(postgres_engine, project) == before


def test_product_decision_roundtrip_and_database_guards(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload, title="A product record remains immutable")
    requested = api.post(gate_path(project, work), json=gate_request(operation_id=uuid4()))
    assert requested.status_code == 201, requested.text
    before = _snapshot(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]),
                    io.BytesIO(_export(postgres_engine, project)))
    after = _snapshot(postgres_engine, project)
    for table in ("project_product_decision_domains", "product_decision_requests", "work_gates",
                  "decision_documents", "decision_revisions", "decision_work_item_links"):
        assert before[table] == after[table]
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("UPDATE product_decision_requests SET gate_id=gate_id"))
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM project_product_decision_domains"))
