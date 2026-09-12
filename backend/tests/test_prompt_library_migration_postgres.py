"""A failed prompt export preserves legacy settings and supports an exact retry."""

import hashlib
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.prompt_storage import PROMPTS, PromptStorage

from .conftest import BACKEND_DIR, TEST_API_KEY

pytestmark = pytest.mark.postgres


@pytest.fixture
def legacy_prompt_database(tmp_path, monkeypatch):
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key=TEST_API_KEY)
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_prompt_migration_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = create_engine(
        url.update_query_dict({"options": f"-c search_path={schema} -c timezone=UTC"}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    monkeypatch.setenv("MNEMONIC_PROMPT_ROOT", str(tmp_path / "prompts"))
    try:
        migrate(engine, "0034_variable_work_leases")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


def migrate(engine, revision):
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def project_settings(engine, project_id):
    with engine.connect() as connection:
        return dict(connection.execute(
            text("SELECT * FROM project_settings WHERE project_id=:id"), {"id": project_id}
        ).mappings().one())


def test_failed_export_preserves_customized_settings_and_exact_retry(
    legacy_prompt_database, tmp_path, monkeypatch,
):
    engine = legacy_prompt_database
    project_id = uuid4()
    recall = "  Recall $PROJECT_NAME.\r\n\nKeep this spacing exactly.  "
    report = "Résumé for $WORK_ITEM_TITLE.\r\nPreserve this final newline.\n"
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:id,'Export test','export-test')"),
            {"id": project_id},
        )
        connection.execute(
            text("UPDATE project_settings SET recall_pointer_template=:recall, "
                 "job_completion_report_prompt=:report,revision=revision+1 WHERE project_id=:id"),
            {"id": project_id, "recall": recall, "report": report},
        )
    before = project_settings(engine, project_id)
    original_read = PromptStorage.read

    def interrupted_read(self, selected_project, prompt_id):
        if prompt_id == "cold-code-review":
            raise OSError("Injected export interruption")
        return original_read(self, selected_project, prompt_id)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(PromptStorage, "read", interrupted_read)
        with pytest.raises(OSError, match="Injected export interruption"):
            migrate(engine, "head")
    assert project_settings(engine, project_id) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0034_variable_work_leases"
        )
    folder = tmp_path / "prompts" / "projects" / str(project_id)
    recall_path = folder / "recall-pointer.md"
    report_path = folder / "job-completion-report.md"
    assert recall_path.read_bytes() == recall.encode()
    assert report_path.read_bytes() == report.encode()

    # Existing files from a different edit must never be silently overwritten on retry.
    recall_path.write_bytes(b"An external edit after the failed migration.")
    with pytest.raises(OSError, match="differs from the migration export"):
        migrate(engine, "head")
    assert project_settings(engine, project_id) == before
    recall_path.write_bytes(recall.encode())
    migrate(engine, "head")

    after = project_settings(engine, project_id)
    assert "recall_pointer_template" not in after
    assert "job_completion_report_prompt" not in after
    assert after["revision"] == before["revision"]
    assert after["recall_pointer_sha256"] == hashlib.sha256(recall.encode()).hexdigest()
    assert after["job_completion_report_prompt_sha256"] == (
        hashlib.sha256(report.encode()).hexdigest()
    )
    assert recall_path.read_bytes() == recall.encode()
    assert report_path.read_bytes() == report.encode()
    assert {path.stem for path in folder.glob("*.md")} == set(PROMPTS)
    with engine.connect() as connection:
        columns = set(connection.scalars(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='job_completion_reports'"
        )))
    assert {"prompt_text", "prompt_sha256", "prompt_template_sha256"} <= columns


def test_changed_shipped_default_preserves_existing_project_and_seeds_new_projects(
    legacy_prompt_database, tmp_path, monkeypatch,
):
    from mnemonic_api import prompt_storage

    engine = legacy_prompt_database
    original = prompt_storage.default_prompt
    old_report = original("job-completion-report")
    replacement = "A new shipped report template for future projects."
    old_project, new_project = uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:id,'Original','original')"),
            {"id": old_project},
        )

    def changed_default(prompt_id):
        return replacement if prompt_id == "job-completion-report" else original(prompt_id)

    monkeypatch.setattr(prompt_storage, "default_prompt", changed_default)
    migrate(engine, "head")
    storage = PromptStorage(tmp_path / "prompts")
    assert storage.read(old_project, "job-completion-report").content == old_report
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO projects(id,name,slug) VALUES(:id,'New project','new-project')"),
            {"id": new_project},
        )
    assert storage.read(new_project, "job-completion-report").content == replacement
    assert project_settings(engine, new_project)["job_completion_report_prompt_sha256"] == (
        hashlib.sha256(replacement.encode()).hexdigest()
    )
