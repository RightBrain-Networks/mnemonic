"""Direct SQL cannot bypass review policy, immutable results or remediation ancestry."""

import importlib.util
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .code_review_database_fixtures import close_work, create_work, finish_review, policy
from .code_review_fixtures import handoff
from .conftest import BACKEND_DIR
from .test_phase6_migration_postgres import (
    empty_phase6_migration_engine as empty_phase6_migration_engine,
)
from .test_phase12_database_postgres import _close, _project, _work

pytestmark = pytest.mark.postgres


@pytest.fixture
def requested_review(api, project, work_payload, checkpoint_fields):
    policy(api, project)
    work = create_work(api, project, work_payload)
    closed = close_work(api, project, work, checkpoint_fields)
    return closed["work_item"], closed["code_review_request"]


def test_database_byte_accounting_matches_canonical_utf8_json(postgres_engine: Engine):
    payload = {"text": "Café\n東京", "quotes": '"\\', "count": 123, "flags": [True, None]}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with postgres_engine.connect() as connection:
        actual, size = connection.execute(
            text(
                "SELECT mnemonic_code_review_canonical_json(CAST(:value AS jsonb)),"
                "mnemonic_code_review_content_bytes(CAST(:value AS jsonb))"
            ),
            {"value": json.dumps(payload)},
        ).one()
    assert actual == canonical
    assert size == len(canonical.encode("utf-8"))


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE code_reviews SET scope_sha256=repeat('c',64) WHERE id=:review",
        "UPDATE code_reviews SET state='completed',version=2 WHERE id=:review",
        "UPDATE code_reviews SET state='superseded',version=2 WHERE id=:review",
        "DELETE FROM code_reviews WHERE id=:review",
        "DELETE FROM code_review_scopes WHERE review_id=:review",
        "UPDATE code_review_handoffs SET change_summary='Changed' WHERE review_id=:review",
        "DELETE FROM work_completion_review_policies WHERE work_item_id=:work",
        "UPDATE work_items SET remediation_depth=2,remediation_id=gen_random_uuid() WHERE id=:work",
        "UPDATE work_items SET completion_review_checkpoint_id=NULL WHERE id=:work",
        "UPDATE checkpoints SET requires_code_review_policy=false WHERE work_item_id=:work",
        "TRUNCATE code_reviews CASCADE",
        "TRUNCATE code_review_handoffs CASCADE",
    ],
)
def test_review_history_and_state_cannot_be_forged(
    postgres_engine: Engine, requested_review, mutation
):
    work, review = requested_review
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(mutation), {"work": work["id"], "review": review["id"]})
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT state FROM code_reviews WHERE id=:id"), {"id": review["id"]}
            )
            == "requested"
        )


@pytest.mark.parametrize(
    "changes",
    [
        "code_review_required_min_priority=3,revision=revision+1",
        "code_review_optional_min_priority=105,revision=revision+1",
        "code_review_required_min_priority=50",
        "code_review_policy_touched=true",
    ],
)
def test_settings_require_valid_thresholds_and_database_witness(
    postgres_engine: Engine,
    project,
    changes,
):
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE project_settings SET {changes} WHERE project_id=:id"),
            {"id": project["id"]},
        )


def test_policy_touch_is_monotonic_even_after_defaults_restored(
    api, project, postgres_engine: Engine
):
    policy(api, project, required=50)
    policy(api, project, required=100)
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT code_review_policy_touched FROM project_settings WHERE project_id=:id"
                ),
                {"id": project["id"]},
            )
            is True
        )
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE project_settings SET code_review_policy_touched=false WHERE project_id=:id"
            ),
            {"id": project["id"]},
        )


def test_second_generation_is_unreviewable_and_lineage_is_permanent(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine: Engine,
):
    policy(api, project, remediation=True)
    original = create_work(api, project, work_payload)
    closed = close_work(api, project, original, checkpoint_fields)
    first = finish_review(api, project, closed["work_item"], closed["code_review_request"], count=3)
    remediation = first["remediation_work"]["work_item"]
    assert first["remediation"]["depth"] == 1
    closed = close_work(api, project, remediation, checkpoint_fields)
    second = finish_review(api, project, closed["work_item"], closed["code_review_request"])
    second_work = second["remediation_work"]["work_item"]
    assert second["remediation"]["depth"] == 2
    done = close_work(api, project, second_work, checkpoint_fields, review=False)
    assert done["review_policy_decision"]["decision"] == "ineligible_depth_limit"
    assert "code_review_request" not in done
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM work_items WHERE project_id=:id"), {"id": project["id"]}
            )
            == 3
        )
        assert connection.scalar(text("SELECT count(*) FROM code_review_findings")) == 4
    for mutation in (
        "UPDATE work_items SET remediation_depth=0,remediation_id=NULL WHERE id=:id",
        "DELETE FROM code_review_remediations WHERE remediation_work_item_id=:id",
        "DELETE FROM work_relationships WHERE id=(SELECT relationship_id FROM "
        "code_review_remediations WHERE remediation_work_item_id=:id)",
        "UPDATE code_review_remediations SET depth=1,parent_remediation_id=NULL "
        "WHERE remediation_work_item_id=:id",
    ):
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text(mutation), {"id": second_work["id"]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("object_format", None),
        ("repository_key", None),
        ("head_commit", "b" * 7),
        ("repository_url", "https://example.com/repo?token=x"),
        ("checkout_path", "/tmp/evil\nquery Mnemonic"),
        ("checkout_path", "relative/repo"),
    ],
)
def test_sql_scope_validation_rejects_invalid_or_context_bearing_data(
    postgres_engine: Engine,
    field,
    value,
):
    scope = deepcopy(handoff()["scope"]["repositories"])
    scope[0][field] = value
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT mnemonic_code_review_scope_valid(CAST(:scope AS jsonb))"),
                {"scope": json.dumps(scope)},
            )
            is False
        )


def _migrate(engine: Engine, target: str, *, down: bool = False) -> None:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, target)


def test_untouched_migration_round_trip_preserves_existing_settings(empty_phase6_migration_engine):
    engine = empty_phase6_migration_engine
    _migrate(engine, "0022_external_references")
    project_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:id,'Existing','existing')"),
            {"id": project_id},
        )
        before = connection.scalar(
            text("SELECT to_jsonb(s) FROM project_settings s WHERE project_id=:id"),
            {"id": project_id},
        )
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT code_review_required_min_priority, "
                "code_review_optional_min_priority,allow_remediation_code_reviews,"
                "code_review_policy_touched "
                "FROM project_settings WHERE project_id=:id"
            ),
            {"id": project_id},
        ).one() == (100, 100, False, False)
    _migrate(engine, "0022_external_references", down=True)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT to_jsonb(s) FROM project_settings s WHERE project_id=:id"),
                {"id": project_id},
            )
            == before
        )


def test_changed_then_reset_policy_prevents_downgrade(empty_phase6_migration_engine):
    engine = empty_phase6_migration_engine
    _migrate(engine, "head")
    project_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:id,'Existing','existing')"),
            {"id": project_id},
        )
        for threshold in (50, 100):
            connection.execute(
                text(
                    "UPDATE project_settings SET code_review_required_min_priority=:n, "
                    "revision=revision+1 WHERE project_id=:id"
                ),
                {"n": threshold, "id": project_id},
            )
    with pytest.raises(RuntimeError, match="downgrade refused"):
        _migrate(engine, "0022_external_references", down=True)


@pytest.mark.parametrize("depart", [False, True])
def test_new_done_requires_policy_before_commit_or_departure(postgres_engine: Engine, depart):
    with pytest.raises(DBAPIError), Session(postgres_engine) as database, database.begin():
        work = _work(database, _project(database))
        _close(database, work, "done", with_review_policy=False)
        if depart:
            work.title = "A changed title cannot leave the unsealed episode"
            database.flush()


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE code_review_results SET summary='Edited' WHERE id=:id",
        "DELETE FROM code_review_results WHERE id=:id",
        "UPDATE code_review_findings SET finding_key='F999' WHERE result_id=:id",
        "DELETE FROM code_review_findings WHERE result_id=:id",
        "UPDATE code_review_remediations SET root_work_item_id=gen_random_uuid() "
        "WHERE result_id=:id",
        "TRUNCATE code_review_results CASCADE",
        "TRUNCATE code_review_remediations CASCADE",
    ],
)
def test_finished_result_and_findings_are_immutable(
    api,
    project,
    requested_review,
    postgres_engine: Engine,
    mutation,
):
    work, review = requested_review
    finished = finish_review(api, project, work, review, count=2)
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(mutation), {"id": finished["result"]["id"]})


def test_audit_is_read_only_and_keeps_pending_and_expired_states_operational(
    api,
    project,
    requested_review,
    postgres_engine: Engine,
):
    work, review = requested_review
    finish_review(api, project, work, review, count=2)
    path = BACKEND_DIR.parent / "scripts" / "audit_code_reviews.py"
    spec = importlib.util.spec_from_file_location("code_review_audit_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with postgres_engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        result = module.audit(connection)
        assert result["ok"], result
        assert all(count == 0 for count in result["findings"].values())
        assert result["operational_counts"]["reviews_completed"] == 1
        assert result["operational_counts"]["remediation_items"] == 1
        encoded = json.dumps(result)
        assert "src/cache.py" not in encoded and "lease_token" not in encoded
