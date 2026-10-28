"""Bounded checks that existing database content still points at mounted stores."""

import hashlib
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import Connection, inspect, text

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.prompt_storage import PROMPTS, default_prompt
from mnemonic_api.transcript_storage import _open_source

SAMPLE_LIMIT = 100


def _readable_file(root: Path, relative: str) -> bool:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return False
    try:
        descriptor = _open_source(str(root / path), [root])
    except (OSError, ValueError, ExtractionError):
        return False
    os.close(descriptor)
    return True


def _require_matching_file(root: Path, pointers: list[str], label: str) -> None:
    if pointers and not any(_readable_file(root, pointer) for pointer in pointers):
        raise RuntimeError(
            f"Database references {label}, but none of {len(pointers)} sampled files "
            "is readable in its mounted store. The store may be empty, incorrect, or "
            "inaccessible. Restore the original bind mounts with compose.bind-mounts.yaml "
            "or inspect the sampled store before startup."
        )


def _prompt_pointers(connection: Connection) -> list[str]:
    inspector = inspect(connection)
    if not inspector.has_table("project_settings"):
        return []
    columns = {column["name"] for column in inspector.get_columns("project_settings")}
    if "job_completion_report_prompt_sha256" not in columns:
        return []
    # Untouched defaults are allowed to materialize lazily. Customized templates
    # live on disk; only the priority rubric moved into PostgreSQL in migration 51.
    rows = connection.execute(text(
        "SELECT project_id FROM project_settings WHERE prompt_context_revision > 1 "
        "OR recall_pointer_sha256 <> :recall OR job_completion_report_prompt_sha256 <> :report "
        "ORDER BY project_id LIMIT :limit"
    ), {"recall": hashlib.sha256(default_prompt("recall-pointer").encode()).hexdigest(),
        "report": hashlib.sha256(default_prompt("job-completion-report").encode()).hexdigest(),
        "limit": SAMPLE_LIMIT}).scalars()
    return [f"projects/{UUID(str(project))}/{prompt}.md" for project in rows for prompt in PROMPTS]


def require_existing_storage(connection: Connection, stores: dict[str, Path]) -> None:
    """Detect wholly wrong stores without making one missing file a global outage.

    This is a mount guard, not an integrity audit. Sample at most 100 retained
    database pointers per binary store, stopping at the first readable match.
    An explicit error describes the limited sample when no match is found.
    """
    inspector = inspect(connection)
    checks = [
        ("artifacts", "relative_path", "revision > 0 AND deleted_at IS NULL"),
        ("transcripts", "storage_key", "copy_status = 'ready' AND storage_key IS NOT NULL"),
    ]
    for table, pointer, predicate in checks:
        if not inspector.has_table(table):
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if pointer not in columns:
            continue
        pointers = list(connection.execute(text(
            f"SELECT {pointer} FROM {table} WHERE {predicate} ORDER BY id LIMIT :limit"
        ), {"limit": SAMPLE_LIMIT}).scalars())
        _require_matching_file(stores[table], pointers, table)
    _require_matching_file(stores["prompts"], _prompt_pointers(connection), "customized prompts")
