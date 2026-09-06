"""Operational Phase 12 audit and restore-incarnation regression coverage."""

import runpy
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .code_review_database_fixtures import close_work, create_work, finish_review, policy
from .conftest import BACKEND_DIR, reset_disposable_schema
from .historical_review_audit_fixtures import seal_historical_receipt
from .test_completion_evidence_postgres import (
    _insert_direct_artifact,
    _insert_direct_completion_checkpoint,
    _insert_direct_completion_event,
    _insert_direct_observation,
    _transition_direct_completion_to_done,
)
from .test_phase12_database_postgres import _close, _project, _work

pytestmark = pytest.mark.postgres


def _audit(engine: Engine, expected_head: str | None = None):
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        if expected_head is None:
            return audit["audit_snapshot"](connection)
        return audit["audit_snapshot"](connection, expected_head)


def _review_audit(engine: Engine):
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_code_reviews.py"))
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return audit["audit"](connection)


def _pre_review_completion(engine: Engine, project: dict, work: dict) -> dict:
    """Create genuine 0023 completion history that predates review policy witnesses."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0023_work_item_moves")
    with engine.begin() as connection:
        checkpoint = _insert_direct_completion_checkpoint(connection, work["id"])
        _insert_direct_observation(connection, project["id"], work["id"], checkpoint)
        _insert_direct_artifact(connection, project["id"], work["id"], checkpoint)
        version = _transition_direct_completion_to_done(connection, work["id"])
        _insert_direct_completion_event(
            connection, project["id"], work["id"], checkpoint, version, seal_review_policy=False
        )
        receipt_id = seal_historical_receipt(connection, work["id"], checkpoint["id"])
        receipt_before = connection.scalar(
            text("SELECT response_body::text FROM client_operations WHERE id=:id"),
            {"id": receipt_id},
        )
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        assert connection.scalar(
            text("SELECT response_body::text FROM client_operations WHERE id=:id"),
            {"id": receipt_id},
        ) == receipt_before
        assert connection.scalar(text("SELECT count(*) FROM work_completion_review_policies")) == 0
    return {**work, "version": version, "status": "done"}


def test_project_activity_audit_accepts_valid_populated_history(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        _close(database, _work(database, project_id), "done")
    report = _audit(postgres_engine)
    assert report["result"] == "pass", report
    assert report["inventory"]["reports"] == 1
    assert "The font request" not in str(report)


def test_project_activity_audit_accepts_review_events_and_checks_review_facts(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    policy(api, project)
    work = create_work(api, project, work_payload)
    completed = close_work(api, project, work, checkpoint_fields)
    finish_review(api, project, completed["work_item"], completed["code_review_request"], count=2)
    policy(api, project, required=100, optional=0)
    question_work = create_work(api, project, work_payload)
    close_work(api, project, question_work, checkpoint_fields, review=False)
    report = _audit(postgres_engine)
    assert report["result"] == "pass", report
    assert report["expected_head"] == "0025_cross_project_relationships"


def test_project_and_review_audits_keep_supported_0024_boundary(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    try:
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0024_code_reviews")

        aggregate = _audit(postgres_engine, "0024_code_reviews")
        review = _review_audit(postgres_engine)

        assert aggregate["result"] == "pass", aggregate
        assert aggregate["expected_head"] == "0024_code_reviews"
        assert review["ok"], review
        assert review["schema_head"] == "0024_code_reviews"
    finally:
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")


def test_project_activity_audit_detects_count_and_guard_tampering(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        _close(database, _work(database, project_id))
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE project_job_completion_report_counts "
                "DISABLE TRIGGER job_report_count_guard"
            )
        )
        connection.execute(
            text("UPDATE project_job_completion_report_counts SET undismissed_count=9")
        )
    try:
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"]["report_count_drift"] == 1
        assert report["blocking_findings"]["catalog_triggers_drift"] == 1
    finally:
        reset_disposable_schema(postgres_engine)


def test_offline_stream_rotation_preserves_all_facts_and_head(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        _close(database, _work(database, project_id))
    with postgres_engine.begin() as connection:
        before = connection.execute(
            text(
                "SELECT stream_id,last_sequence,historical_through_sequence "
                "FROM project_activity_heads WHERE project_id=:p"
            ),
            {"p": project_id},
        ).one()
        entries = (
            connection.execute(
                text(
                    "SELECT row_to_json(a)::text FROM project_activity a "
                    "ORDER BY project_id,sequence"
                )
            )
            .scalars()
            .all()
        )
        assert (
            connection.scalar(text("SELECT mnemonic_rotate_activity_streams_after_restore()")) == 1
        )
        after = connection.execute(
            text(
                "SELECT stream_id,last_sequence,historical_through_sequence "
                "FROM project_activity_heads WHERE project_id=:p"
            ),
            {"p": project_id},
        ).one()
        assert before.stream_id != after.stream_id
        assert before[1:] == after[1:]
        assert (
            connection.execute(
                text(
                    "SELECT row_to_json(a)::text FROM project_activity a "
                    "ORDER BY project_id,sequence"
                )
            )
            .scalars()
            .all()
            == entries
        )
    assert _audit(postgres_engine)["result"] == "pass"


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
def test_audit_accepts_current_report_receipts(
    api, project, work_payload, checkpoint_fields, postgres_engine, outcome
):
    from .test_project_reports_postgres import close, create

    work = create(api, project, work_payload)
    response, _ = close(api, project, work, checkpoint_fields, outcome)
    assert response.status_code == 200, response.text
    result = _audit(postgres_engine)
    assert result["result"] == "pass", result


def test_audit_detects_unreported_closeout_after_guards_are_restored(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work_id = _work(database, project_id).id
    guards = (
        ("work_items", "job_report_transition_guard"),
        ("work_items", "job_report_transition_sealed"),
        ("work_events", "job_report_event_guard"),
    )
    try:
        with postgres_engine.begin() as connection:
            for table, trigger in guards:
                connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
            connection.execute(
                text("UPDATE work_items SET status='wont-do',version=2 WHERE id=:w"), {"w": work_id}
            )
            connection.execute(
                text("""
                INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,
                                        actor_client,actor_session_id,metadata)
                VALUES(:p,:w,'work_status_changed','client','pytest','guard-restoration',
                    '{"changes":{"status":{"before":"pending","after":"wont-do"}},
                      "from_status":"pending","to_status":"wont-do","work_version": 2}'::jsonb)
            """),
                {"p": project_id, "w": work_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            for table, trigger in guards:
                connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"]["missing_live_closeout_reports"] == 1
        assert "catalog_triggers_drift" not in report["blocking_findings"]
    finally:
        reset_disposable_schema(postgres_engine)


@pytest.mark.parametrize(
    ("mutation", "category"),
    [
        (
            "GRANT EXECUTE ON FUNCTION mnemonic_guard_completion_checkpoint_insert() TO PUBLIC",
            "function_permissions",
        ),
        ("GRANT SELECT ON job_completion_reports TO PUBLIC", "relation_state"),
        ("GRANT SELECT (summary) ON job_completion_reports TO PUBLIC", "column_permissions"),
        ("ALTER TABLE job_completion_reports DISABLE TRIGGER ALL", "foreign_key_triggers"),
    ],
)
def test_audit_preserves_permission_and_fk_trigger_checks(
    postgres_engine: Engine, mutation: str, category: str
):
    reset_disposable_schema(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(mutation))
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"][f"catalog_{category}_drift"] > 0
    finally:
        reset_disposable_schema(postgres_engine)


@pytest.mark.parametrize(
    ("mutation", "category"),
    [
        ("DROP INDEX ix_work_items_external_references", "indexes"),
        ("ALTER TABLE work_items ALTER COLUMN external_references DROP NOT NULL", "columns"),
        (
            "ALTER TABLE work_items DROP CONSTRAINT ck_work_items_external_references_valid",
            "constraints",
        ),
        ("ALTER FUNCTION mnemonic_external_references_is_valid(jsonb) VOLATILE", "functions"),
        (
            "ALTER TABLE work_events DROP CONSTRAINT ck_work_events_metadata_envelope_valid",
            "constraints",
        ),
    ],
)
def test_external_reference_audit_rejects_new_guard_drift(postgres_engine, mutation, category):
    reset_disposable_schema(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(mutation))
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"][f"catalog_{category}_drift"] > 0
    finally:
        reset_disposable_schema(postgres_engine)


def test_external_reference_audit_finds_invalid_data_after_guard_restoration(postgres_engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work_id = _work(database, project_id).id
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE work_items DROP CONSTRAINT ck_work_items_external_references_valid"
                )
            )
            connection.execute(
                text("UPDATE work_items SET external_references='[{}]'::jsonb WHERE id=:id"),
                {"id": work_id},
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(
                text(
                    "ALTER TABLE work_items ADD CONSTRAINT ck_work_items_external_references_valid "
                    "CHECK (mnemonic_external_references_is_valid(external_references)) NOT VALID"
                )
            )
        report = _audit(postgres_engine)
        assert report["blocking_findings"]["invalid_external_reference_lists"] == 1
        assert report["blocking_findings"]["catalog_constraints_drift"] > 0
    finally:
        reset_disposable_schema(postgres_engine)


def test_audit_preserves_explicit_report_head_support(postgres_engine):
    from alembic import command
    from alembic.config import Config

    reset_disposable_schema(postgres_engine)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))
    try:
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0021_job_completion_reports")
        with postgres_engine.connect() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            report = audit["audit_snapshot"](connection, "0021_job_completion_reports")
        assert report["result"] == "pass", report
        assert "reports" in report["inventory"]
        # Current-head mismatches fail before selecting new columns/functions.
        with postgres_engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            current = audit["audit_snapshot"](connection)
        assert current["blocking_findings"] == {"migration_head_mismatch": 1}
    finally:
        reset_disposable_schema(postgres_engine)


def test_external_event_audit_validates_full_shape_after_function_restoration(postgres_engine):
    import re

    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work_id = _work(database, project_id).id
    try:
        with postgres_engine.begin() as connection:
            definition = connection.scalar(
                text(
                    "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n "
                    "ON n.oid=p.pronamespace WHERE n.nspname=current_schema() "
                    "AND p.proname='mnemonic_work_event_metadata_v1_is_valid'"
                )
            )
            relaxed = re.sub(
                r"AS \$function\$.*\$function\$",
                "AS $function$BEGIN RETURN true; END$function$",
                definition,
                flags=re.S,
            )
            assert relaxed != definition
            connection.execute(text(relaxed))
            connection.execute(
                text("""
                INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,
                                        actor_client,actor_session_id,metadata)
                VALUES (:p,:w,'work_updated','client','pytest','guard-restoration',
                  CAST(:metadata AS jsonb))
            """),
                {
                    "p": project_id,
                    "w": work_id,
                    "metadata": '{"work_version":2,"changes":{"external_references":'
                    '{"before":[],"after":[],"extra":true}}}',
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(text(definition))
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"]["invalid_external_event_metadata"] == 1
        assert not any(key.startswith("catalog_") for key in report["blocking_findings"])
    finally:
        reset_disposable_schema(postgres_engine)


def _create_api_project(api, name: str) -> dict:
    response = api.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def _create_and_move_work(api, source: dict, target: dict, work_payload: dict) -> dict:
    created = api.post(
        f"/api/v1/projects/{source['id']}/work-items",
        json=work_payload,
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    moved = api.post(
        f"/api/v1/projects/{source['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": work["version"],
            "client_operation_id": str(uuid4()),
            "actor": {
                "actor_client": "dashboard",
                "actor_session_id": "move-audit-tests",
                "actor_model": "pytest",
            },
        },
    )
    assert moved.status_code == 200, moved.text
    return moved.json()


def test_project_activity_audit_accepts_valid_move_history(
    api, project, work_payload, postgres_engine
):
    target = _create_api_project(api, "Audit move destination")
    moved = _create_and_move_work(api, project, target, work_payload)

    report = _audit(postgres_engine)

    assert report["result"] == "pass", report
    assert report["inventory"]["moves"] == 1
    assert report["prior_phase_counts"]["event_owner_violations"] == 0
    assert moved["work_item"]["project_id"] == target["id"]


def test_project_activity_audit_tracks_cross_project_relationship_authority(
    api, project, work_payload, postgres_engine
):
    target_project = _create_api_project(api, "Audit relationship counterpart")
    moved_project = _create_api_project(api, "Audit relationship moved endpoint")

    source_response = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Cross-project audit source"},
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()["work_item"]
    target_response = api.post(
        f"/api/v1/projects/{target_project['id']}/work-items",
        json={**work_payload, "title": "Cross-project audit target"},
    )
    assert target_response.status_code == 201, target_response.text
    target = target_response.json()["work_item"]

    relationship_response = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "related",
            "source_work_item_id": source["id"],
            "target_work_item_id": target["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "cross-project-audit",
        },
    )
    assert relationship_response.status_code == 200, relationship_response.text
    relationship = relationship_response.json()["relationship"]
    moved_response = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{source['id']}/move",
        json={
            "target_project_id": moved_project["id"],
            "expected_version": source["version"],
        },
    )
    assert moved_response.status_code == 200, moved_response.text

    report = _audit(postgres_engine)

    assert report["result"] == "pass", report
    assert report["inventory"]["relationships"] == 1
    assert report["inventory"]["cross_project_relationships"] == 1
    assert report["prior_phase_counts"]["relationship_scope_violations"] == 0
    assert report["prior_phase_counts"]["event_owner_violations"] == 0

    unrelated = _create_api_project(api, "Invalid relationship authority")
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_relationships DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE work_relationships SET project_id=:project_id WHERE id=:id"),
            {"project_id": unrelated["id"], "id": relationship["id"]},
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(text("ALTER TABLE work_relationships ENABLE TRIGGER USER"))

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["blocking_findings"]["invalid_retained_relationship_facts"] == 1


def test_project_activity_audit_accepts_origin_receipts_after_move(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    actor = {
        "actor_client": "dashboard",
        "actor_session_id": "move-receipt-audit",
        "actor_model": "pytest",
    }
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "client_operation_id": str(uuid4())},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    checkpoint = api.post(
        f"{endpoint}/checkpoints",
        json={
            **checkpoint_fields,
            "kind": "progress",
            "prompt": "Preserve this source-project checkpoint receipt.",
            "client_operation_id": str(uuid4()),
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    updated = api.patch(
        endpoint,
        json={
            "expected_version": work["version"],
            "summary": "Preserve this source-project update receipt.",
            "actor": actor,
            "client_operation_id": str(uuid4()),
        },
    )
    assert updated.status_code == 200, updated.text
    target = _create_api_project(api, "Historical receipt destination")
    moved = api.post(
        f"{endpoint}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": updated.json()["version"],
            "actor": actor,
            "client_operation_id": str(uuid4()),
        },
    )
    assert moved.status_code == 200, moved.text
    checkpoint = api.post(
        f"/api/v1/projects/{target['id']}/work-items/{work['id']}/checkpoints",
        json={
            **checkpoint_fields,
            "kind": "progress",
            "prompt": "A checkpoint created after the first move.",
        },
    )
    assert checkpoint.status_code == 201, checkpoint.text
    final_project = _create_api_project(api, "Second reopened-work destination")
    second_move = api.post(
        f"/api/v1/projects/{target['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": final_project["id"],
            "expected_version": moved.json()["work_item"]["version"],
        },
    )
    assert second_move.status_code == 200, second_move.text

    report = _audit(postgres_engine)
    assert report["result"] == "pass", report
    with postgres_engine.connect() as connection:
        origin_kinds = set(
            connection.execute(
                text(
                    "SELECT operation_kind FROM client_operations "
                    "WHERE project_id=:project_id AND state='completed'"
                ),
                {"project_id": project["id"]},
            ).scalars()
        )
    assert {"create_work", "add_checkpoint", "update_work", "move_work"} <= origin_kinds

    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE client_operations DISABLE TRIGGER client_operation_mutation_guard")
        )
        connection.execute(
            text(
                "UPDATE client_operations SET project_id=:target_project_id "
                "WHERE operation_kind='add_checkpoint'"
            ),
            {"target_project_id": target["id"]},
        )
        connection.execute(
            text("ALTER TABLE client_operations ENABLE TRIGGER client_operation_mutation_guard")
        )
    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["prior_phase_counts"]["checkpoint_receipt_scope_violation_count"] == 1


def test_project_activity_audit_tracks_reopen_owner_through_move_chain(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    from .test_project_reports_postgres import create

    work = create(api, project, work_payload)
    closed_work = _pre_review_completion(postgres_engine, project, work)
    reopened = api.patch(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}",
        json={"expected_version": closed_work["version"], "status": "pending"},
    )
    assert reopened.status_code == 200, reopened.text
    target = _create_api_project(api, "Reopened work destination")
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": reopened.json()["version"],
        },
    )
    assert moved.status_code == 200, moved.text

    report = _audit(postgres_engine)
    assert report["result"] == "pass", report["blocking_findings"]
    assert report["prior_phase_counts"]["reopen_binding_violation_count"] == 0

    with postgres_engine.begin() as connection:
        reopen_event_id = connection.scalar(
            text(
                "SELECT id FROM work_events "
                "WHERE work_item_id=:work_item_id AND event_type='work_reopened'"
            ),
            {"work_item_id": work["id"]},
        )
        target_sequence = connection.scalar(
            text("SELECT last_sequence+1 FROM project_activity_heads WHERE project_id=:project_id"),
            {"project_id": target["id"]},
        )
        connection.execute(text("ALTER TABLE work_events DISABLE TRIGGER ALL"))
        connection.execute(text("ALTER TABLE project_activity DISABLE TRIGGER ALL"))
        connection.execute(
            text("UPDATE work_events SET project_id=:project_id WHERE id=:event_id"),
            {"project_id": target["id"], "event_id": reopen_event_id},
        )
        connection.execute(
            text(
                "UPDATE project_activity SET project_id=:project_id,sequence=:sequence "
                "WHERE work_event_id=:event_id"
            ),
            {
                "project_id": target["id"],
                "sequence": target_sequence,
                "event_id": reopen_event_id,
            },
        )
        connection.execute(text("ALTER TABLE project_activity ENABLE TRIGGER ALL"))
        connection.execute(text("ALTER TABLE work_events ENABLE TRIGGER ALL"))

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["prior_phase_counts"]["event_owner_violations"] == 1
    assert corrupted["prior_phase_counts"]["reopen_binding_violation_count"] == 1
    assert "missing_work_event_activity" not in corrupted["blocking_findings"]


def test_project_activity_audit_rejects_unresolved_gate_in_historical_project(
    api, project, work_payload, postgres_engine
):
    from .test_human_gates_postgres import gate_request, resolution_payload

    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json=work_payload,
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    source_gate_path = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/gates"
    requested = api.post(source_gate_path, json=gate_request())
    assert requested.status_code == 201, requested.text
    gate = requested.json()
    resolved = api.post(
        f"{source_gate_path}/{gate['id']}/resolve",
        json=resolution_payload(revision=gate["current_context_revision"]),
    )
    assert resolved.status_code == 200, resolved.text
    target = _create_api_project(api, "Resolved gate destination")
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": work["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    initial_report = _audit(postgres_engine)
    assert initial_report["result"] == "pass", initial_report

    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_gates DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE work_gates SET project_id=:project_id WHERE id=:gate_id"),
            {"project_id": target["id"], "gate_id": gate["id"]},
        )
        connection.execute(text("ALTER TABLE work_gates ENABLE TRIGGER USER"))
    relabeled_resolved = _audit(postgres_engine)
    assert relabeled_resolved["result"] == "blocked", relabeled_resolved
    assert relabeled_resolved["prior_phase_counts"]["gate_owner_violations"] == 1
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_gates DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE work_gates SET project_id=:project_id WHERE id=:gate_id"),
            {"project_id": project["id"], "gate_id": gate["id"]},
        )
        connection.execute(text("ALTER TABLE work_gates ENABLE TRIGGER USER"))
    assert _audit(postgres_engine)["result"] == "pass"

    current_gate_path = f"/api/v1/projects/{target['id']}/work-items/{work['id']}/gates"
    current_gate = api.post(current_gate_path, json=gate_request())
    assert current_gate.status_code == 201, current_gate.text
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_gates DISABLE TRIGGER USER"))
        connection.execute(
            text("UPDATE work_gates SET project_id=:project_id WHERE id=:gate_id"),
            {"project_id": project["id"], "gate_id": current_gate.json()["id"]},
        )
        connection.execute(text("ALTER TABLE work_gates ENABLE TRIGGER USER"))

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["prior_phase_counts"]["gate_owner_violations"] == 1


def test_project_activity_audit_rejects_later_relationship_endpoint(
    api, project, work_payload, postgres_engine
):
    def create(title: str) -> dict:
        response = api.post(
            f"/api/v1/projects/{project['id']}/work-items",
            json={**work_payload, "title": title},
        )
        assert response.status_code == 201, response.text
        return response.json()["work_item"]

    source = create("Relationship audit source")
    counterpart = create("Relationship audit counterpart")
    relationship = api.post(
        f"/api/v1/projects/{project['id']}/relationships",
        json={
            "relationship_type": "parent-child",
            "source_work_item_id": source["id"],
            "target_work_item_id": counterpart["id"],
            "created_by_client": "pytest",
            "created_by_session_id": "relationship-owner-audit",
        },
    )
    assert relationship.status_code == 200, relationship.text
    later_work = create("Work created after the relationship event")
    initial_report = _audit(postgres_engine)
    assert initial_report["result"] == "pass", initial_report

    with postgres_engine.begin() as connection:
        event_id = connection.scalar(
            text(
                "SELECT id FROM work_events "
                "WHERE work_item_id=:work_item_id "
                "AND relationship_id=:relationship_id "
                "AND event_type='relationship_added'"
            ),
            {
                "work_item_id": source["id"],
                "relationship_id": relationship.json()["relationship"]["id"],
            },
        )
        connection.execute(text("ALTER TABLE work_events DISABLE TRIGGER events_immutable"))
        connection.execute(
            text(
                "UPDATE work_events SET relationship_target_work_item_id=:later_work_id "
                "WHERE id=:event_id"
            ),
            {"later_work_id": later_work["id"], "event_id": event_id},
        )
        connection.execute(text("ALTER TABLE work_events ENABLE TRIGGER events_immutable"))

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["prior_phase_counts"]["event_owner_violations"] == 1
    assert corrupted["blocking_findings"]["invalid_retained_relationship_facts"] == 1
    assert corrupted["blocking_findings"]["invalid_relationship_event_pairs"] == 1


@pytest.mark.parametrize(
    ("table_name", "trigger_name"),
    [
        ("verification_results", "verification_results_immutable"),
        ("artifact_references", "artifact_references_immutable"),
    ],
)
def test_project_activity_audit_binds_evidence_to_completion_event_project(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
    table_name: str,
    trigger_name: str,
):
    from .test_project_reports_postgres import create

    work = create(api, project, work_payload)
    closed_work = _pre_review_completion(postgres_engine, project, work)
    target = _create_api_project(api, "Completion evidence destination")
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": closed_work["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    assert _audit(postgres_engine)["result"] == "pass"

    with postgres_engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"))
        connection.execute(
            text(
                f"UPDATE {table_name} SET project_id=:target_project_id "
                "WHERE id=(SELECT id FROM "
                f"{table_name} WHERE work_item_id=:work_item_id ORDER BY id LIMIT 1)"
            ),
            {
                "target_project_id": target["id"],
                "work_item_id": work["id"],
            },
        )
        connection.execute(text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"))

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["prior_phase_counts"]["evidence_owner_violation_count"] == 1
    assert corrupted["prior_phase_counts"]["unsealed_completion_episode_count"] == 1


def test_project_activity_audit_binds_lease_renewal_to_claim_origin(
    api, project, work_payload, postgres_engine
):
    created = api.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={**work_payload, "title": "Lease renewal origin audit"},
    )
    assert created.status_code == 201, created.text
    work = created.json()["work_item"]
    endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    claimed = api.post(
        endpoint + "/claim",
        json={
            "holder_client": "pytest",
            "holder_session_id": "lease-renewal-owner-audit",
            "claim_request_id": "lease-renewal-owner-audit",
        },
    )
    assert claimed.status_code == 200, claimed.text
    lease_token = claimed.json()["lease_token"]
    renewed = api.post(endpoint + "/renew-claim", json={"lease_token": lease_token})
    assert renewed.status_code == 200, renewed.text
    released = api.post(endpoint + "/release-claim", json={"lease_token": lease_token})
    assert released.status_code == 200, released.text
    target = _create_api_project(api, "Lease renewal destination")
    moved = api.post(
        endpoint + "/move",
        json={
            "target_project_id": target["id"],
            "expected_version": work["version"],
        },
    )
    assert moved.status_code == 200, moved.text
    assert _audit(postgres_engine)["result"] == "pass"

    with postgres_engine.begin() as connection:
        target_sequence = connection.scalar(
            text("SELECT last_sequence+1 FROM project_activity_heads WHERE project_id=:project_id"),
            {"project_id": target["id"]},
        )
        connection.execute(
            text("ALTER TABLE project_activity DISABLE TRIGGER project_activity_immutable")
        )
        connection.execute(
            text(
                "UPDATE project_activity "
                "SET project_id=:target_project_id,sequence=:target_sequence "
                "WHERE kind='lease_renewed' AND work_item_id=:work_item_id"
            ),
            {
                "target_project_id": target["id"],
                "target_sequence": target_sequence,
                "work_item_id": work["id"],
            },
        )
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(
            text("ALTER TABLE project_activity ENABLE TRIGGER project_activity_immutable")
        )

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["blocking_findings"]["invalid_lease_renewal_source"] == 1


def test_project_activity_audit_accepts_moved_report_follow_up(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    from .test_project_reports_postgres import actor, close, create

    source_work = create(api, project, work_payload)
    closed, _ = close(api, project, source_work, checkpoint_fields)
    assert closed.status_code == 200, closed.text
    report_id = closed.json()["job_completion_report"]["id"]
    followed = api.post(
        f"/api/v1/projects/{project['id']}/job-completion-reports/{report_id}/follow-ups",
        json={
            "client_operation_id": str(uuid4()),
            "actor": actor(checkpoint_fields),
            "title": "Retain provenance after moving this follow-up",
            "summary": "The origin report remains in its source project.",
            "initial_checkpoint": checkpoint_fields,
        },
    )
    assert followed.status_code == 201, followed.text
    follow_up_work = followed.json()["work_item"]
    follow_up_closed, _ = close(api, project, follow_up_work, checkpoint_fields, "wont-do")
    assert follow_up_closed.status_code == 200, follow_up_closed.text
    follow_up_work = follow_up_closed.json()
    second_report_id = follow_up_closed.json()["job_completion_report"]["id"]
    chained_checkpoint = {
        **checkpoint_fields,
        "source_session_id": "audit-provenance-chain",
    }
    chained = api.post(
        f"/api/v1/projects/{project['id']}/job-completion-reports/{second_report_id}/follow-ups",
        json={
            "client_operation_id": str(uuid4()),
            "actor": actor(chained_checkpoint),
            "title": "Second-generation provenance",
            "summary": "This work is both a follow-up and a report source.",
            "initial_checkpoint": chained_checkpoint,
        },
    )
    assert chained.status_code == 201, chained.text
    target = _create_api_project(api, "Moved follow-up destination")
    moved = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{follow_up_work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": follow_up_work["version"],
            "client_operation_id": str(uuid4()),
            "actor": actor(checkpoint_fields),
        },
    )
    assert moved.status_code == 200, moved.text

    report = _audit(postgres_engine)

    assert report["result"] == "pass", report
    assert "invalid_follow_up_provenance" not in report["blocking_findings"]
    assert "work_provenance_prefix_mismatch" not in report["blocking_findings"]
    assert report["inventory"]["work_provenance_heads"] == 3

    unrelated = api.post(
        f"/api/v1/projects/{target['id']}/work-items",
        json={**work_payload, "title": "Unrelated later follow-up work"},
    )
    assert unrelated.status_code == 201, unrelated.text
    unrelated_work = unrelated.json()["work_item"]
    follow_up_id = followed.json()["follow_up"]["id"]
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE project_activity DISABLE TRIGGER project_activity_immutable")
        )
        connection.execute(
            text(
                "UPDATE project_activity SET work_item_id=:work_item_id "
                "WHERE job_completion_report_id=:report_id "
                "AND kind='job_completion_report_created'"
            ),
            {"work_item_id": unrelated_work["id"], "report_id": second_report_id},
        )
        connection.execute(
            text("ALTER TABLE project_activity ENABLE TRIGGER project_activity_immutable")
        )
    corrupted_report_activity = _audit(postgres_engine)
    assert corrupted_report_activity["result"] == "blocked", corrupted_report_activity
    assert corrupted_report_activity["blocking_findings"]["missing_or_mismatched_review"] == 1
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE project_activity DISABLE TRIGGER project_activity_immutable")
        )
        connection.execute(
            text(
                "UPDATE project_activity SET work_item_id=:work_item_id "
                "WHERE job_completion_report_id=:report_id "
                "AND kind='job_completion_report_created'"
            ),
            {"work_item_id": follow_up_work["id"], "report_id": second_report_id},
        )
        connection.execute(
            text("ALTER TABLE project_activity ENABLE TRIGGER project_activity_immutable")
        )
    assert _audit(postgres_engine)["result"] == "pass"

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE job_completion_report_follow_ups DISABLE TRIGGER job_report_immutable"
            )
        )
        connection.execute(
            text("ALTER TABLE project_activity DISABLE TRIGGER project_activity_immutable")
        )
        connection.execute(
            text(
                "UPDATE job_completion_report_follow_ups "
                "SET follow_up_work_item_id=:work_item_id WHERE id=:follow_up_id"
            ),
            {"work_item_id": unrelated_work["id"], "follow_up_id": follow_up_id},
        )
        connection.execute(
            text(
                "UPDATE project_activity SET work_item_id=:work_item_id "
                "WHERE follow_up_id=:follow_up_id"
            ),
            {"work_item_id": unrelated_work["id"], "follow_up_id": follow_up_id},
        )
        connection.execute(
            text("ALTER TABLE project_activity ENABLE TRIGGER project_activity_immutable")
        )
        connection.execute(
            text("ALTER TABLE job_completion_report_follow_ups ENABLE TRIGGER job_report_immutable")
        )

    corrupted = _audit(postgres_engine)
    assert corrupted["result"] == "blocked", corrupted
    assert corrupted["blocking_findings"]["invalid_follow_up_provenance"] == 1


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        (
            """
            UPDATE work_report_provenance_heads
            SET last_sequence=last_sequence+1
            WHERE work_item_id=(
                SELECT source_work_item_id
                FROM job_completion_report_follow_ups LIMIT 1
            )
            """,
            "work_provenance_prefix_mismatch",
        ),
        (
            """
            UPDATE job_completion_report_follow_ups
            SET created_at=(
                SELECT min(created_at)-interval '1 second'
                FROM job_completion_report_follow_ups
            )
            WHERE id=(
                SELECT id FROM job_completion_report_follow_ups
                ORDER BY source_work_sequence DESC LIMIT 1
            )
            """,
            "work_provenance_order_mismatch",
        ),
    ],
)
def test_project_activity_audit_detects_provenance_sequence_corruption(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
    mutation: str,
    finding: str,
):
    from .test_project_reports_postgres import actor, close, create

    source_work = create(api, project, work_payload)
    closed, _ = close(api, project, source_work, checkpoint_fields)
    report_id = closed.json()["job_completion_report"]["id"]
    path = f"/api/v1/projects/{project['id']}/job-completion-reports/{report_id}/follow-ups"
    for index in range(2):
        follow_up_checkpoint = {
            **checkpoint_fields,
            "source_session_id": f"audit-provenance-{index}",
        }
        followed = api.post(
            path,
            json={
                "client_operation_id": str(uuid4()),
                "actor": actor(follow_up_checkpoint),
                "title": f"Audit provenance sequence {index}",
                "summary": "Create enough provenance to verify a strict prefix and order.",
                "initial_checkpoint": follow_up_checkpoint,
            },
        )
        assert followed.status_code == 201, followed.text

    table = (
        "work_report_provenance_heads"
        if finding == "work_provenance_prefix_mismatch"
        else "job_completion_report_follow_ups"
    )
    trigger = (
        "work_report_provenance_head_guard"
        if finding == "work_provenance_prefix_mismatch"
        else "job_report_immutable"
    )
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
            connection.execute(text(mutation))
            connection.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
        report = _audit(postgres_engine)
        assert report["result"] == "blocked"
        assert report["blocking_findings"][finding] > 0
        assert not any(key.startswith("catalog_") for key in report["blocking_findings"])
    finally:
        reset_disposable_schema(postgres_engine)


@pytest.mark.parametrize(
    ("table_name", "trigger_name", "mutation", "finding"),
    [
        (
            "work_events",
            "events_immutable",
            """
                UPDATE work_events
                SET actor_session_id='corrupted-move-event'
                WHERE work_move_id=(SELECT id FROM work_item_moves LIMIT 1)
                  AND metadata->>'role'='source'
            """,
            "invalid_move_event_pairs",
        ),
        (
            "work_items",
            "work_project_move_guard",
            "UPDATE work_items SET project_id=:wrong_project_id WHERE id=:work_item_id",
            "invalid_move_chain",
        ),
        (
            "client_operations",
            "client_operation_mutation_guard",
            """
                UPDATE client_operations
                SET response_body=jsonb_set(
                    response_body,
                    '{target_project_id}',
                    to_jsonb(CAST(:wrong_project_id AS text)),
                    false
                )
                WHERE operation_kind='move_work'
            """,
            "invalid_move_receipts",
        ),
        (
            "client_operations",
            "client_operation_mutation_guard",
            """
                UPDATE client_operations
                SET response_body=jsonb_set(
                    response_body,
                    '{work_item,updated_at}',
                    '"2000-01-01T00:00:00Z"'::jsonb,
                    false
                )
                WHERE operation_kind='move_work'
            """,
            "invalid_move_receipts",
        ),
    ],
)
def test_project_activity_audit_detects_move_corruption_after_guard_restoration(
    api,
    project,
    work_payload,
    postgres_engine,
    table_name: str,
    trigger_name: str,
    mutation: str,
    finding: str,
):
    target = _create_api_project(api, "Corruption audit move destination")
    moved = _create_and_move_work(api, project, target, work_payload)
    wrong_project = _create_api_project(api, "Corruption audit unrelated project")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}"))
            connection.execute(
                text(mutation),
                {
                    "work_item_id": moved["work_item"]["id"],
                    "wrong_project_id": wrong_project["id"],
                },
            )
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            connection.execute(text(f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"))

        report = _audit(postgres_engine)

        assert report["result"] == "blocked"
        assert report["blocking_findings"][finding] > 0
        assert not any(key.startswith("catalog_") for key in report["blocking_findings"])
    finally:
        reset_disposable_schema(postgres_engine)
