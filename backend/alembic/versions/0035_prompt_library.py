"""Move editable prompt templates to Markdown files; retain immutable report history."""

import hashlib
import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op

from mnemonic_api.job_report_defaults import DEFAULT_JOB_COMPLETION_REPORT_PROMPT
from mnemonic_api.prompt_storage import PROMPTS, PromptStorage, default_prompt

revision = "0035_prompt_library"
down_revision = "0034_variable_work_leases"
branch_labels = None
depends_on = None


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _function(name: str, replacements: list[tuple[str, str]]) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(bind.scalar(sa.text("SELECT current_schema()")))
    definition = bind.scalar(
        sa.text("SELECT pg_get_functiondef(CAST(:name AS regprocedure))"),
        {"name": f"{schema}.{name}()"},
    )
    if not isinstance(definition, str):
        raise RuntimeError("Missing predecessor prompt integrity function")
    for old, new in replacements:
        if old not in definition:
            raise RuntimeError(f"Unexpected predecessor prompt integrity function: {name}")
        definition = definition.replace(old, new)
    op.execute(sa.text(definition))


def _export(storage: PromptStorage) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT project_id, recall_pointer_template, job_completion_report_prompt "
            "FROM project_settings ORDER BY project_id"
        )
    ).mappings()
    for row in rows:
        for prompt_id in PROMPTS:
            if prompt_id not in {"recall-pointer", "job-completion-report"}:
                storage.read(row["project_id"], prompt_id)
                continue
            value = default_prompt(prompt_id)
            if prompt_id == "recall-pointer" and row["recall_pointer_template"] is not None:
                value = row["recall_pointer_template"]
            elif prompt_id == "job-completion-report":
                value = row["job_completion_report_prompt"]
            storage.seed(row["project_id"], prompt_id, value)
        bind.execute(
            sa.text(
                "UPDATE project_settings SET recall_pointer_sha256=:recall, "
                "job_completion_report_prompt_sha256=:report WHERE project_id=:project"
            ),
            {
                "project": row["project_id"],
                "recall": _digest(
                    row["recall_pointer_template"] or default_prompt("recall-pointer")
                ),
                "report": _digest(row["job_completion_report_prompt"]),
            },
        )
    op.execute("UPDATE job_completion_reports SET prompt_template_sha256=prompt_sha256")


def _guards() -> None:
    report_default = default_prompt("job-completion-report")
    literal = "'" + DEFAULT_JOB_COMPLETION_REPORT_PROMPT.replace("'", "''") + "'"
    recall_hash = _digest(default_prompt("recall-pointer"))
    report_hash = _digest(report_default)
    _function(
        "mnemonic_guard_job_report_settings",
        [
            ("changed:=ROW(NEW.", "changed:=ROW(NEW.prompt_context_revision,NEW."),
            (
                "ROW(OLD.lease_default_minutes,",
                "ROW(OLD.prompt_context_revision,OLD.lease_default_minutes,",
            ),
            ("NEW.recall_pointer_template IS NULL", f"NEW.recall_pointer_sha256='{recall_hash}'"),
            ("recall_pointer_template", "recall_pointer_sha256"),
            ("job_completion_report_prompt", "job_completion_report_prompt_sha256"),
            (literal, f"'{report_hash}'"),
        ],
    )
    _function(
        "mnemonic_job_report_project_source",
        [
            (
                "project_settings(project_id,job_completion_report_prompt)",
                "project_settings(project_id,recall_pointer_sha256,job_completion_report_prompt_sha256)",
            ),
            (f"VALUES(NEW.id,{literal})", f"VALUES(NEW.id,'{recall_hash}','{report_hash}')"),
        ],
    )
    _function(
        "mnemonic_guard_job_report_insert",
        [
            (
                "NEW.prompt_text<>settings.job_completion_report_prompt",
                "NEW.prompt_template_sha256<>settings.job_completion_report_prompt_sha256",
            ),
        ],
    )


def upgrade() -> None:
    op.execute("LOCK TABLE project_settings, job_completion_reports IN ACCESS EXCLUSIVE MODE")
    storage = PromptStorage(
        Path(os.environ.get("MNEMONIC_PROMPT_ROOT", "/var/lib/mnemonic/prompts"))
    )
    op.add_column(
        "project_settings",
        sa.Column("prompt_context_revision", sa.BigInteger(), nullable=False, server_default="1"),
    )
    for name in ("recall_pointer_sha256", "job_completion_report_prompt_sha256"):
        op.add_column("project_settings", sa.Column(name, sa.String(64)))
    op.add_column("job_completion_reports", sa.Column("prompt_template_sha256", sa.String(64)))
    op.execute("ALTER TABLE job_completion_reports DISABLE TRIGGER USER")
    _export(storage)
    op.execute("ALTER TABLE job_completion_reports ENABLE TRIGGER USER")
    _guards()
    for name in ("recall_pointer_sha256", "job_completion_report_prompt_sha256"):
        op.alter_column("project_settings", name, nullable=False)
    op.alter_column("job_completion_reports", "prompt_template_sha256", nullable=False)
    for name in (
        "report_prompt_valid",
        "recall_pointer_template_nonblank",
        "recall_pointer_template_max_length",
    ):
        op.drop_constraint(f"ck_project_settings_{name}", "project_settings")
    for name in ("prompt_valid",):
        op.drop_constraint(f"ck_job_completion_reports_{name}", "job_completion_reports")
    op.drop_column("project_settings", "recall_pointer_template")
    op.drop_column("project_settings", "job_completion_report_prompt")
    for table, column, name in (
        ("project_settings", "recall_pointer_sha256", "recall_pointer_hash"),
        ("project_settings", "job_completion_report_prompt_sha256", "report_prompt_hash"),
        ("job_completion_reports", "prompt_template_sha256", "prompt_template_hash"),
    ):
        op.create_check_constraint(name, table, f"{column} ~ '^[a-f0-9]{{64}}$'")

    op.create_check_constraint(
        "prompt_valid",
        "job_completion_reports",
        "length(prompt_text) BETWEEN 1 AND 100000 AND octet_length(prompt_text) <= 400000 "
        "AND mnemonic_has_non_whitespace(prompt_text)",
    )


def _restore_templates(storage: PromptStorage) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT * FROM project_settings ORDER BY project_id")).mappings()
    for row in rows:
        recall = storage.read(row["project_id"], "recall-pointer")
        report = storage.read(row["project_id"], "job-completion-report")
        changed = (
            recall.revision != row["recall_pointer_sha256"]
            or report.revision != row["job_completion_report_prompt_sha256"]
        )
        if changed:
            raise RuntimeError(
                "Read project settings to synchronize external edits before downgrade"
            )
        bind.execute(
            sa.text(
                "UPDATE project_settings SET recall_pointer_template=:recall, "
                "job_completion_report_prompt=:report WHERE project_id=:id"
            ),
            {
                "id": row["project_id"],
                "report": report.content,
                "recall": None
                if recall.content == default_prompt("recall-pointer")
                else recall.content,
            },
        )


def _restore_guards() -> None:
    literal = "'" + DEFAULT_JOB_COMPLETION_REPORT_PROMPT.replace("'", "''") + "'"
    recall_hash = _digest(default_prompt("recall-pointer"))
    report_hash = _digest(default_prompt("job-completion-report"))
    _function(
        "mnemonic_guard_job_report_settings",
        [
            ("NEW.prompt_context_revision,", ""),
            ("OLD.prompt_context_revision,", ""),
            (f"NEW.recall_pointer_sha256='{recall_hash}'", "NEW.recall_pointer_template IS NULL"),
            ("recall_pointer_sha256", "recall_pointer_template"),
            ("job_completion_report_prompt_sha256", "job_completion_report_prompt"),
            (f"'{report_hash}'", literal),
        ],
    )
    _function(
        "mnemonic_job_report_project_source",
        [
            (
                "project_settings(project_id,recall_pointer_sha256,job_completion_report_prompt_sha256)",
                "project_settings(project_id,job_completion_report_prompt)",
            ),
            (f"VALUES(NEW.id,'{recall_hash}','{report_hash}')", f"VALUES(NEW.id,{literal})"),
        ],
    )
    _function(
        "mnemonic_guard_job_report_insert",
        [
            (
                "NEW.prompt_template_sha256<>settings.job_completion_report_prompt_sha256",
                "NEW.prompt_text<>settings.job_completion_report_prompt",
            ),
        ],
    )


def downgrade() -> None:
    op.execute("LOCK TABLE project_settings, job_completion_reports IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM job_completion_reports WHERE NOT "
            "mnemonic_job_report_text_valid_v1(prompt_text,8000,16384,true))"
        )
    ):
        raise RuntimeError("Expanded report history cannot fit the previous prompt schema")
    op.add_column("project_settings", sa.Column("recall_pointer_template", sa.Text()))
    op.add_column("project_settings", sa.Column("job_completion_report_prompt", sa.Text()))
    storage = PromptStorage(
        Path(os.environ.get("MNEMONIC_PROMPT_ROOT", "/var/lib/mnemonic/prompts"))
    )
    _restore_templates(storage)
    _restore_guards()
    op.alter_column("project_settings", "job_completion_report_prompt", nullable=False)
    for name in ("recall_pointer_hash", "report_prompt_hash"):
        op.drop_constraint(f"ck_project_settings_{name}", "project_settings")
    for column in (
        "recall_pointer_sha256",
        "job_completion_report_prompt_sha256",
        "prompt_context_revision",
    ):
        op.drop_column("project_settings", column)
    op.drop_constraint("ck_job_completion_reports_prompt_template_hash", "job_completion_reports")
    op.drop_constraint("ck_job_completion_reports_prompt_valid", "job_completion_reports")
    op.drop_column("job_completion_reports", "prompt_template_sha256")
    for table, name, expression in (
        (
            "project_settings",
            "report_prompt_valid",
            "mnemonic_job_report_text_valid_v1(job_completion_report_prompt,8000,16384,true)",
        ),
        (
            "project_settings",
            "recall_pointer_template_nonblank",
            "mnemonic_has_non_whitespace(recall_pointer_template)",
        ),
        (
            "project_settings",
            "recall_pointer_template_max_length",
            "length(recall_pointer_template)<=100000",
        ),
        (
            "job_completion_reports",
            "prompt_valid",
            "mnemonic_job_report_text_valid_v1(prompt_text,8000,16384,true)",
        ),
    ):
        op.create_check_constraint(name, table, expression)
