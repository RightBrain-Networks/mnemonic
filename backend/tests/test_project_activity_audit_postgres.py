"""Operational Phase 12 audit and restore-incarnation regression coverage."""

import runpy

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .conftest import BACKEND_DIR, reset_disposable_schema
from .test_phase12_database_postgres import _close, _project, _work

pytestmark = pytest.mark.postgres


def _audit(engine: Engine):
    audit = runpy.run_path(str(BACKEND_DIR.parent / "scripts/audit_project_activity.py"))
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return audit["audit_snapshot"](connection)


def test_project_activity_audit_accepts_valid_populated_history(postgres_engine: Engine):
    reset_disposable_schema(postgres_engine)
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        _close(database, _work(database, project_id), "done")
    report = _audit(postgres_engine)
    assert report["result"] == "pass", report
    assert report["inventory"]["reports"] == 1
    assert "The font request" not in str(report)


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
