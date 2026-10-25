"""Inheritance exclusions are additive and cannot erase retained origin-edge policy."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.schemas import WorkCreation, WorkItemCreate
from mnemonic_api.services.client_operations import (
    complete_client_operation,
    prepare_client_operation,
    reserve_client_operation,
)

from .test_decision_changes_postgres import root_review
from .test_decision_reviews_postgres import settings
from .test_product_decision_migration_postgres import migrate
from .test_project_activity_audit_postgres import _audit
from .test_work_inheritance_postgres import excluded
from .test_work_lineage_postgres import collection, create

pytestmark = pytest.mark.postgres
PREVIOUS = "0063_impact_findings"
MIGRATION = "0064_work_change_inheritance"
HEAD = "0065_knowledge_management"
EXCLUSIONS = "work_change_inheritance_exclusions"


def _retained_rows(engine):
    """Read physical historical tables without applying current ORM projections."""
    with engine.connect() as connection:
        names = connection.scalars(text(
            "SELECT tablename FROM pg_tables WHERE schemaname=current_schema() "
            "AND tablename <> 'alembic_version' ORDER BY tablename"
        )).all()
        quote = connection.dialect.identifier_preparer.quote
        return {name: list(connection.scalars(text(
            f"SELECT to_jsonb(row) FROM {quote(name)} row ORDER BY to_jsonb(row)::text"
        ))) for name in names}


def _sparse_receipt(engine, project, work_payload, response):
    """Retain the pre-origin create shape using the permanent receipt journal."""
    intent = {**work_payload, "client_operation_id": str(uuid4())}
    intent.pop("discovered_from_work_item_id")
    assert "inherit_decision_change" not in intent
    assert "decision_change_inheritance_reason" not in intent
    with Session(engine) as database:
        prepared = prepare_client_operation(
            "create_work", UUID(project["id"]), {}, WorkItemCreate.model_validate(intent),
        )
        reserved = reserve_client_operation(database, prepared, wait_seconds=1)
        complete_client_operation(
            database, reserved, WorkCreation.model_validate(response), mutation_applied=True,
        )
        database.commit()
    return intent


def test_upgrade_retains_ordinary_work_lineage_and_sparse_creation_receipts(
    api, project, work_payload, postgres_engine,
):
    ordinary = create(api, project, work_payload)
    sparse = _sparse_receipt(postgres_engine, project, work_payload, ordinary)
    _, _, revision, driver = root_review(api, project)
    child = create(api, project, work_payload,
                   discovered_from_work_item_id=driver["id"])["work_item"]
    grandchild = create(api, project, work_payload,
                        discovered_from_work_item_id=child["id"])["work_item"]
    change_id = revision["change"]["change_id"]
    assert child["decision_change_ids"] == grandchild["decision_change_ids"] == [change_id]

    # Remove the new local policy before crossing its guarded downgrade boundary.
    # Retained review and lineage evidence is independent of future scheduling.
    settings(api, project, automatic_decision_reviews=False, decision_review_priority=40)

    # Default behavior has no 0064-specific rows or fields. Freeze it at the actual
    # preceding schema, then use only physical SQL and migrations until upgraded.
    migrate(postgres_engine, PREVIOUS, downgrade=True)
    before = _retained_rows(postgres_engine)
    assert EXCLUSIONS not in before
    assert {row["work_item_id"] for row in before["decision_change_work"]} == {
        driver["id"], child["id"], grandchild["id"],
    }
    receipt = next(row for row in before["client_operations"]
                   if row["client_operation_id"] == sparse["client_operation_id"])
    assert receipt["operation_kind"] == "create_work" and receipt["state"] == "completed"
    assert "decision_change_inheritance_exclusion" not in receipt["response_body"]["work_item"]
    previous_audit = _audit(postgres_engine, PREVIOUS)
    assert previous_audit["result"] == "pass", previous_audit["blocking_findings"]

    migrate(postgres_engine, MIGRATION)
    after = _retained_rows(postgres_engine)
    assert after.pop(EXCLUSIONS) == []
    assert after == before
    migrate(postgres_engine, HEAD)
    assert create(api, project, sparse) == ordinary
    assert create(api, project, sparse, inherit_decision_change=True) == ordinary
    changed = api.post(collection(project), json={**sparse, "discovered_from_work_item_id": None})
    assert changed.status_code == 409
    current_audit = _audit(postgres_engine)
    assert current_audit["result"] == "pass", current_audit


def test_downgrade_refuses_to_discard_recorded_inheritance_exclusions(
    api, project, work_payload, postgres_engine,
):
    origin = create(api, project, work_payload)["work_item"]
    creation, intent = excluded(api, project, work_payload, origin)
    before = _retained_rows(postgres_engine)
    assert len(before[EXCLUSIONS]) == 1
    with pytest.raises(
        RuntimeError, match="Work inheritance exclusions cannot be safely downgraded",
    ):
        migrate(postgres_engine, PREVIOUS, downgrade=True)
    assert _retained_rows(postgres_engine) == before
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
    assert create(api, project, intent) == creation
    audit = _audit(postgres_engine)
    assert audit["result"] == "pass", audit
