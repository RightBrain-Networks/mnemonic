"""Phase 12 source-trigger, transition, prefix and lossless migration proofs."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.job_report_defaults import DEFAULT_JOB_COMPLETION_REPORT_PROMPT
from mnemonic_api.models import Checkpoint, JobCompletionReport, Project, WorkItem
from mnemonic_api.schemas import MutationActor
from mnemonic_api.services.work_events import (
    database_now,
    stage_work_changed,
    stage_work_completed,
    stage_work_created,
)

from .conftest import BACKEND_DIR
from .test_phase6_migration_postgres import (
    empty_phase6_migration_engine as empty_phase6_migration_engine,
)

pytestmark = pytest.mark.postgres


def _project(database: Session) -> UUID:
    project = Project(name="Phase 12 SQL proof", slug="phase12-" + uuid4().hex)
    database.add(project)
    database.flush()
    return project.id


def _work(database: Session, project_id: UUID) -> WorkItem:
    checkpoint_id = uuid4()
    work = WorkItem(
        project_id=project_id,
        title="Use the selected font",
        summary="Font change",
        initial_checkpoint_id=checkpoint_id,
    )
    database.add(work)
    database.flush()
    checkpoint = Checkpoint(
        id=checkpoint_id,
        work_item_id=work.id,
        prompt="Use Arial.",
        source_client="dashboard",
        source_session_id="phase12-proof",
    )
    database.add(checkpoint)
    database.flush()
    stage_work_created(database, work, checkpoint)
    database.flush()
    return work


def _close(database: Session, work: WorkItem, outcome: str = "wont-do") -> JobCompletionReport:
    report_id = uuid4()
    old_version = work.version
    checkpoint = None
    if outcome == "done":
        checkpoint = Checkpoint(
            work_item_id=work.id,
            kind="completion",
            prompt="Changed the font.",
            source_client="dashboard",
            source_session_id="phase12-proof",
        )
        database.add(checkpoint)
        database.flush()
    work.status = outcome
    work.version += 1
    database.flush()
    if checkpoint is not None:
        event = stage_work_completed(database, work, checkpoint, from_status="pending")
    else:
        event = stage_work_changed(
            database,
            work,
            before={"status": "pending", "version": old_version},
            requested_fields=["status"],
            actor=MutationActor(actor_client="dashboard", actor_session_id="phase12-proof"),
            created_at=database_now(database),
        )
    event.job_completion_report_id = report_id
    database.flush()
    report = JobCompletionReport(
        id=report_id,
        project_id=work.project_id,
        work_item_id=work.id,
        closeout_event_id=event.id,
        closeout_work_version=work.version,
        closeout_status=outcome,
        completion_checkpoint_id=checkpoint.id if checkpoint else None,
        work_title_at_closeout=work.title,
        summary="The font request was reviewed.",
        fyi_items=[],
        prompt_revision=1,
        prompt_text=DEFAULT_JOB_COMPLETION_REPORT_PROMPT,
        prompt_sha256=hashlib.sha256(DEFAULT_JOB_COMPLETION_REPORT_PROMPT.encode()).hexdigest(),
        actor_client="dashboard",
        actor_session_id="phase12-proof",
    )
    database.add(report)
    database.flush()
    return report


@pytest.mark.parametrize("outcome", ["done", "wont-do", "promoted"])
def test_closeout_seals_exact_report_and_derived_review(postgres_engine: Engine, outcome: str):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work = _work(database, project_id)
        report = _close(database, work, outcome)
        report_id = report.id
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT undismissed_count FROM project_job_completion_report_counts "
                    "WHERE project_id=:p"
                ),
                {"p": project_id},
            ).scalar_one()
            == 1
        )
        review = connection.execute(
            text(
                "SELECT follow_up_count,dismissal_id,created_sequence "
                "FROM job_completion_report_reviews WHERE report_id=:r"
            ),
            {"r": report_id},
        ).one()
        assert review[:2] == (0, None)
        assert (
            connection.execute(
                text("SELECT kind FROM project_activity WHERE project_id=:p AND sequence=:s"),
                {"p": project_id, "s": review.created_sequence},
            ).scalar_one()
            == "job_completion_report_created"
        )


@pytest.mark.parametrize(
    "attack",
    [
        "UPDATE work_items SET status='wont-do',version=version+1 WHERE id=:w",
        "UPDATE work_items SET last_reportable_closeout_version=2 WHERE id=:w",
        "UPDATE work_items SET status='promoted' WHERE id=:w",
        "UPDATE work_items SET status='wont-do',version=version+1 WHERE id=:w; UPDATE "
        "work_items SET title='Edited' WHERE id=:w",
        "UPDATE work_items SET status='wont-do',version=version+1 WHERE id=:w; UPDATE "
        "work_items SET status='pending',version=version+1 WHERE id=:w",
    ],
)
def test_unsealed_or_fabricated_transition_rolls_back(postgres_engine: Engine, attack: str):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work_id = _work(database, project_id).id
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        for statement in attack.split("; "):
            connection.execute(text(statement), {"w": work_id})
    with postgres_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT status,version,last_reportable_closeout_version FROM work_items WHERE id=:w"
            ),
            {"w": work_id},
        ).one() == ("pending", 1, None)


@pytest.mark.parametrize("source", ["deferred", "wont-do", "promoted"])
def test_only_pending_can_enter_another_terminal_state(postgres_engine: Engine, source: str):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work = _work(database, project_id)
        if source == "deferred":
            work.status = source
            work.version += 1
            database.flush()
        else:
            _close(database, work, source)
        work_id = work.id
    target = "promoted" if source != "promoted" else "wont-do"
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text("UPDATE work_items SET status=:target,version=version+1 WHERE id=:w"),
            {"w": work_id, "target": target},
        )


@pytest.mark.parametrize(
    "attack",
    [
        "UPDATE project_activity_heads SET last_sequence=last_sequence+1 WHERE project_id=:p",
        "UPDATE project_activity_heads SET stream_id=gen_random_uuid() WHERE project_id=:p",
        "INSERT INTO project_activity(project_id,sequence,kind) VALUES(:p,99,'project_updated')",
        "DELETE FROM project_activity WHERE project_id=:p",
        "TRUNCATE project_activity CASCADE",
        "UPDATE project_job_completion_report_counts SET undismissed_count=42 WHERE project_id=:p",
        "DELETE FROM project_settings WHERE project_id=:p",
        "UPDATE project_settings SET revision=revision+1 WHERE project_id=:p",
        "UPDATE job_completion_reports SET summary='Changed' WHERE project_id=:p",
        "DELETE FROM job_completion_report_reviews WHERE project_id=:p",
        "UPDATE job_completion_report_reviews SET follow_up_count=1 WHERE project_id=:p",
        "TRUNCATE job_completion_reports CASCADE",
    ],
)
def test_direct_history_and_derived_state_attacks_fail(postgres_engine: Engine, attack: str):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        _close(database, _work(database, project_id))
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(attack), {"p": project_id})


def test_dismissal_is_monotonic_and_follow_up_retains_exact_provenance(postgres_engine: Engine):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        source = _work(database, project_id)
        report = _close(database, source)
        report_id, source_id = report.id, source.id
    with Session(postgres_engine) as database, database.begin():
        followup = _work(database, project_id)
        followup_id = followup.id
        association = uuid4()
        database.execute(
            text(
                "INSERT INTO job_completion_report_follow_ups "
                "(id,project_id,report_id,source_work_item_id,follow_up_work_item_id,"
                "actor_client,actor_session_id) "
                "VALUES(:id,:p,:r,:source,:new,'dashboard','phase12-proof')"
            ),
            {
                "id": association,
                "p": project_id,
                "r": report_id,
                "source": source_id,
                "new": followup_id,
            },
        )
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE job_completion_report_reviews SET dismissal_id=:id, "
                "dismissal_actor_client='dashboard',dismissal_actor_session_id='human' "
                "WHERE report_id=:r"
            ),
            {"id": uuid4(), "r": report_id},
        )
    with postgres_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT undismissed_count FROM project_job_completion_report_counts "
                    "WHERE project_id=:p"
                ),
                {"p": project_id},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT follow_up_count FROM job_completion_report_reviews WHERE report_id=:r"
                ),
                {"r": report_id},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT work_item_id FROM project_activity WHERE follow_up_id=:id"),
                {"id": association},
            ).scalar_one()
            == followup_id
        )
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE job_completion_report_reviews SET dismissal_id=NULL, "
                "dismissed_at=NULL,dismissal_actor_client=NULL,"
                "dismissal_actor_session_id=NULL WHERE report_id=:r"
            ),
            {"r": report_id},
        )


def test_settings_defaults_noop_changes_and_reset_independence(postgres_engine: Engine):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
    with postgres_engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT recall_pointer_template,job_completion_report_prompt,revision "
                "FROM project_settings WHERE project_id=:p"
            ),
            {"p": project_id},
        ).one() == (None, DEFAULT_JOB_COMPLETION_REPORT_PROMPT, 1)
        connection.execute(
            text(
                "UPDATE project_settings SET recall_pointer_template='Exact {{title}}', "
                "revision=revision+1 WHERE project_id=:p"
            ),
            {"p": project_id},
        )
        connection.execute(
            text(
                "UPDATE project_settings SET job_completion_report_prompt='Custom prompt', "
                "revision=revision+1 WHERE project_id=:p"
            ),
            {"p": project_id},
        )
        connection.execute(
            text(
                "UPDATE project_settings SET recall_pointer_template=NULL, "
                "revision=revision+1 WHERE project_id=:p"
            ),
            {"p": project_id},
        )
        connection.execute(
            text("UPDATE project_settings SET recall_pointer_template=NULL WHERE project_id=:p"),
            {"p": project_id},
        )
        assert connection.execute(
            text(
                "SELECT job_completion_report_prompt,revision "
                "FROM project_settings WHERE project_id=:p"
            ),
            {"p": project_id},
        ).one() == ("Custom prompt", 4)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM project_activity WHERE project_id=:p "
                    "AND kind='project_settings_updated'"
                ),
                {"p": project_id},
            ).scalar_one()
            == 3
        )


@pytest.mark.parametrize(
    "bad", [" ", "a\nb", "a\u2029b", "a\u202eb", "\u061ca", "a\x7fb", "a" * 2001]
)
def test_sql_report_text_policy_rejects_controls_and_bounds(postgres_engine: Engine, bad: str):
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT mnemonic_job_report_text_valid_v1(:v,2000,8000,false)"), {"v": bad}
            )
            is False
        )


@pytest.mark.parametrize("rollback", [False, True])
def test_counter_writer_waits_for_committed_prefix(postgres_engine: Engine, rollback: bool):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
    first = postgres_engine.connect()
    transaction = first.begin()
    first.execute(text("UPDATE projects SET name='First writer' WHERE id=:p"), {"p": project_id})
    waiting = Event()
    worker_pid: list[int] = []

    def second_writer():
        with postgres_engine.begin() as connection:
            worker_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            waiting.set()
            connection.execute(
                text("UPDATE projects SET description='Second writer' WHERE id=:p"),
                {"p": project_id},
            )

    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(second_writer)
            assert waiting.wait(2)
            with postgres_engine.connect() as reader:
                deadline = time.monotonic() + 2
                while not reader.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid))>0"), {"pid": worker_pid[0]}
                ):
                    assert time.monotonic() < deadline
                assert (
                    reader.scalar(
                        text(
                            "SELECT last_sequence FROM project_activity_heads WHERE project_id=:p"
                        ),
                        {"p": project_id},
                    )
                    == 1
                )
            transaction.rollback() if rollback else transaction.commit()
            future.result(timeout=5)
        with postgres_engine.connect() as reader:
            sequence = (
                reader.execute(
                    text(
                        "SELECT sequence FROM project_activity WHERE project_id=:p "
                        "ORDER BY sequence"
                    ),
                    {"p": project_id},
                )
                .scalars()
                .all()
            )
            assert sequence == ([1, 2] if rollback else [1, 2, 3])
    finally:
        if transaction.is_active:
            transaction.rollback()
        first.close()


def test_preuse_upgrade_downgrade_preserves_custom_recall(empty_phase6_migration_engine: Engine):
    engine = empty_phase6_migration_engine
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    project_id = uuid4()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0019_structured_completion_evidence")
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:p,'Old project','old-project')"),
            {"p": project_id},
        )
        connection.execute(
            text("INSERT INTO project_settings VALUES(:p,' exact {{title}} \n')"), {"p": project_id}
        )
        command.upgrade(config, "head")
        assert (
            connection.scalar(text("SELECT recall_pointer_template FROM project_settings"))
            == " exact {{title}} \n"
        )
        assert connection.scalar(text("SELECT count(*) FROM job_completion_reports")) == 0
        command.downgrade(config, "0019_structured_completion_evidence")
        assert (
            connection.scalar(text("SELECT recall_pointer_template FROM project_settings"))
            == " exact {{title}} \n"
        )


def test_offline_historical_receipt_survives_0019_and_0021(empty_phase6_migration_engine: Engine):
    import json
    import runpy

    from mnemonic_api.schemas import WorkCompletionCreate
    from mnemonic_api.services.client_operations import (
        ReplayedOperation,
        prepare_client_operation,
        reserve_client_operation,
    )

    engine = empty_phase6_migration_engine
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    seeder = runpy.run_path(str(BACKEND_DIR.parent / "scripts/seed_e2e_historical_completion.py"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0018_repository_freshness")
        fixture = seeder["seed_historical_completion"](connection, uuid4())
        receipt_before = connection.scalar(
            text("SELECT response_body::text FROM client_operations")
        )
        command.upgrade(config, "head")
        assert (
            connection.scalar(text("SELECT response_body::text FROM client_operations"))
            == receipt_before
        )
        assert connection.scalar(text("SELECT count(*) FROM job_completion_reports")) == 0
        assert (
            connection.scalar(
                text("SELECT historical_through_sequence FROM project_activity_heads")
            )
            == 2
        )
        checkpoint_generation, event_id = connection.execute(
            text(
                "SELECT checkpoint.completion_generation,event.id "
                "FROM checkpoints checkpoint JOIN work_events event "
                "ON event.checkpoint_id=checkpoint.id "
                "WHERE event.event_type='work_completed'"
            )
        ).one()
        assert checkpoint_generation == -event_id
    historical = fixture["historicalCompletion"]
    payload = WorkCompletionCreate.model_validate(historical["requestBody"])
    prepared = prepare_client_operation(
        "complete_work",
        UUID(fixture["projectId"]),
        {"work_item_id": UUID(historical["workItemId"])},
        payload,
    )
    with Session(engine) as database:
        replay = reserve_client_operation(database, prepared, wait_seconds=2)
        assert isinstance(replay, ReplayedOperation)
        assert replay.response.body.decode() == historical["responseBody"]
        assert "job_completion_report" not in json.loads(replay.response.body)
        database.commit()


def _raw_activity_event(connection, project_id, work_id):
    connection.execute(
        text("""
        INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,metadata)
        VALUES(:p,:w,'work_updated','unattributed',
            '{"changes":{"title":{"before":"Before","after":"After"}},"work_version": 2}'::jsonb)
    """),
        {"p": project_id, "w": work_id},
    )


@pytest.mark.parametrize("rollback", [False, True])
def test_allocator_serializes_distinct_work_facts_without_project_row_lock(
    postgres_engine: Engine, rollback: bool
):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        first_id, second_id = _work(database, project_id).id, _work(database, project_id).id
        independent_project = _project(database)
        independent_work = _work(database, independent_project).id
    first = postgres_engine.connect()
    transaction = first.begin()
    _raw_activity_event(first, project_id, first_id)
    waiting = Event()
    worker_pid = []

    def second_writer():
        with postgres_engine.begin() as connection:
            worker_pid.append(connection.scalar(text("SELECT pg_backend_pid()")))
            connection.execute(text("SET LOCAL lock_timeout='5s'"))
            waiting.set()
            _raw_activity_event(connection, project_id, second_id)

    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(second_writer)
            assert waiting.wait(2)
            with postgres_engine.connect() as reader:
                deadline = time.monotonic() + 2
                while not reader.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid))>0"), {"pid": worker_pid[0]}
                ):
                    assert time.monotonic() < deadline
                assert (
                    reader.scalar(
                        text(
                            "SELECT last_sequence FROM project_activity_heads WHERE project_id=:p"
                        ),
                        {"p": project_id},
                    )
                    == 3
                )
            # A separate project's head remains writable while both same-project writers wait.
            with postgres_engine.begin() as independent:
                independent.execute(text("SET LOCAL lock_timeout='500ms'"))
                _raw_activity_event(independent, independent_project, independent_work)
            transaction.rollback() if rollback else transaction.commit()
            future.result(timeout=5)
        with postgres_engine.connect() as reader:
            assert reader.execute(
                text("SELECT sequence FROM project_activity WHERE project_id=:p ORDER BY sequence"),
                {"p": project_id},
            ).scalars().all() == ([1, 2, 3, 4] if rollback else [1, 2, 3, 4, 5])
    finally:
        if transaction.is_active:
            transaction.rollback()
        first.close()
