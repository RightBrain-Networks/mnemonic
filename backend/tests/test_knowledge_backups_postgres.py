"""Backups retain every decision revision and reject forged graph history atomically."""

import hashlib
import io
from uuid import UUID, uuid4

import pytest

from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_search_postgres import domain, put
from .test_project_backup_archive import _export, _rewrite, _snapshot

pytestmark = pytest.mark.postgres


def test_backup_retains_text_history_scope_and_hash_idempotence(api, project, postgres_engine):
    selected = domain(api, project)
    original = put(api, project, selected)
    put(api, project, selected, decision_id=original["decision_id"], expected_revision=0,
        content="Amended decision with café", state="partially-superseded")
    scope_path = (f"/api/v1/projects/{project['id']}/knowledge/decisions/"
                  f"{original['decision_id']}/scope")
    response = api.post(scope_path, json={"revision": 0, "commit_hash": "a" * 40,
                                         "client_operation_id": str(uuid4())})
    assert response.status_code == 200, response.text
    snapshot = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    put(api, project, selected, title="A later decision", content="Later text")
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = _snapshot(postgres_engine, project)
    for table in ("knowledge_domains", "decision_documents", "decision_revisions",
                  "decision_relationships", "decision_scopes"):
        assert restored[table] == snapshot[table]
    replay = put(api, project, selected)
    assert replay["reference"] == original["reference"]
    assert len(restored["decision_revisions"]) == 2


def test_backup_refuses_invalid_ordinal_allocation_without_mutating_database(
    api, project, postgres_engine,
):
    put(api, project, domain(api, project))
    original = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)

    def forge(_header, rows):
        next(row for row in rows["knowledge_domains"] if row["prefix"] == "ADR")[
            "next_ordinal"
        ] = 1

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    assert _snapshot(postgres_engine, project) == original


def _append_scope(api, project, decision):
    response = api.post(
        f"/api/v1/projects/{project['id']}/knowledge/decisions/{decision['decision_id']}/scope",
        json={"revision": 0, "commit_hash": "a" * 40, "client_operation_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("field", ["content", "title", "state", "commit_hash"])
def test_backup_refuses_rewritten_history_even_when_table_constraints_pass(
    api, project, postgres_engine, field,
):
    original = put(api, project, domain(api, project))
    _append_scope(api, project, original)
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)

    def forge(_header, rows):
        revision = rows["decision_revisions"][0]
        if field == "content":
            revision["content"] = "Forged but internally consistent UTF-8 content: café."
            encoded = revision["content"].encode("utf-8")
            revision["size_bytes"] = len(encoded)
            revision["sha256"] = hashlib.sha256(encoded).hexdigest()
        elif field == "commit_hash":
            rows["decision_scopes"][0]["commit_hash"] = "b" * 40
        else:
            revision[field] = "rejected" if field == "state" else "Forged title"

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    assert _snapshot(postgres_engine, project) == before


@pytest.mark.parametrize(
    "kind", ["create_knowledge_domain", "put_decision", "append_decision_scope"],
)
def test_backup_refuses_history_without_its_creation_receipt(
    api, project, postgres_engine, kind,
):
    original = put(api, project, domain(api, project))
    _append_scope(api, project, original)
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)

    def forge(_header, rows):
        rows["client_operations"] = [
            receipt for receipt in rows["client_operations"] if receipt["operation_kind"] != kind
        ]

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, forge)))
    assert _snapshot(postgres_engine, project) == before


def test_backup_receipts_allow_later_labels_backlinks_scope_and_idempotent_operations(
    api, project, postgres_engine,
):
    selected = domain(api, project)
    original = put(api, project, selected)
    response = api.patch(
        f"/api/v1/projects/{project['id']}/knowledge/domains/{selected['id']}",
        json={"name": "Architecture decisions", "expected_version": 1,
              "client_operation_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    put(api, project, selected, title="Replacement policy", content="A successor document.",
        related_decision_ids=[original["decision_id"]], supersedes=[original["decision_id"]])
    assert _append_scope(api, project, original)["created"] is True
    assert _append_scope(api, project, original)["created"] is False
    retained = put(api, project, selected, title="Ignored fresh upload metadata", state="rejected")
    assert retained["reference"] == original["reference"]
    assert retained["domain_name"] != original["domain_name"]
    assert retained["scope_count"] == 1 and original["scope_count"] == 0
    assert retained["referenced_by"] == retained["superseded_by"] == ["ADR-00002-0"]
    before = _snapshot(postgres_engine, project)
    restore_project(
        postgres_engine, UUID(project["id"]), io.BytesIO(_export(postgres_engine, project)),
    )
    restored = _snapshot(postgres_engine, project)
    for table in ("knowledge_domains", "decision_documents", "decision_revisions",
                  "decision_relationships", "decision_scopes", "client_operations"):
        assert restored[table] == before[table]
