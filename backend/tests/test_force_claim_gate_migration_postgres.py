"""Gate acquisition requires exact force intent and preserves history across upgrades."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_artifact_extraction_migration_postgres import migrate
from .test_force_claims_postgres import force_payload
from .test_human_gates_postgres import gate_request
from .test_leases_postgres import create_work, item_path
from .test_progress_lease_postgres import retained

pytestmark = pytest.mark.postgres


def guard_definition(engine):
    with engine.connect() as connection:
        return connection.scalar(text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname=current_schema() "
            "AND p.proname='mnemonic_reject_gated_lease_acquisition'"
        ))


@pytest.mark.parametrize("same_request", [False, True])
@pytest.mark.parametrize("same_generation", [False, True])
def test_direct_gated_acquisition_cannot_reuse_a_retained_force_intent(
    api, project, work_payload, postgres_engine, same_request, same_generation,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    assert api.post(path + "/claim", json=force_payload("original")).status_code == 200
    before = retained(postgres_engine, work["id"])
    assert api.post(path + "/gates", json=gate_request()).status_code == 201
    with pytest.raises(DBAPIError, match="unresolved work gate prevents lease acquisition"):
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "UPDATE work_leases SET claim_request_id=:request, "
                "lease_generation_id=:generation, lease_token=:token WHERE work_item_id=:work"
            ), {
                "request": "original" if same_request else "unrecorded",
                "generation": before["lease_generation_id"] if same_generation else uuid4(),
                "token": str(uuid4()), "work": UUID(work["id"]),
            })
    assert retained(postgres_engine, work["id"]) == before


def test_gate_guard_upgrade_and_downgrade_preserve_retained_history(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    assert api.post(path + "/claim", json=force_payload("original")).status_code == 200
    assert api.post(path + "/gates", json=gate_request()).status_code == 201
    before = retained(postgres_engine, work["id"])
    gates = api.get(path + "/gates").json()
    with postgres_engine.connect() as connection:
        journal = connection.execute(text("SELECT * FROM work_force_claims")).all()
    upgraded = guard_definition(postgres_engine)
    migrate(postgres_engine, "0071_automatic_review_cycles", downgrade=True)
    original = guard_definition(postgres_engine)
    assert "work_force_claims" not in original
    migrate(postgres_engine, "head")
    assert guard_definition(postgres_engine) == upgraded
    migrate(postgres_engine, "0071_automatic_review_cycles", downgrade=True)
    assert guard_definition(postgres_engine) == original
    migrate(postgres_engine, "head")
    assert retained(postgres_engine, work["id"]) == before
    assert api.get(path + "/gates").json() == gates
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM work_force_claims")).all() == journal
    recovered = api.post(path + "/claim", json=force_payload("recovered"))
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["lease_token"] != before["lease_token"]
