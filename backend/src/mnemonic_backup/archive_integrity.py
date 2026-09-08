"""Reuse the independently maintained semantic integrity audits before restore commit."""

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import Connection, text

from mnemonic_backup.archive_schema import BackupError


def _audit_module() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / "audit_project_activity.py"
    spec = importlib.util.spec_from_file_location("mnemonic_backup_integrity_audit", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise BackupError(503, "backup_audit_unavailable", "Restore validation is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_integrity(connection: Connection) -> None:
    audit = _audit_module()
    audit.pin_session_settings(connection)
    previous = audit._legacy()
    schema = connection.scalar(text("SELECT current_schema()"))
    counts = previous._base_counts(connection, schema)
    counts.update(previous._core_counts(connection))
    counts.update(previous._repository_freshness_counts(connection))
    counts.update(previous._completion_evidence_counts(connection, schema))
    audit._move_aware_prior_counts(connection, counts, previous, cross_project_relationships=True)
    findings = previous._blocking_counts(counts)
    checks = dict(audit._review_checks())
    for name in ("_ACTIVITY_FINDINGS", "_REPORT_FINDINGS", "_REFERENCE_FINDINGS",
                 "_MOVE_FINDINGS", "_CROSS_PROJECT_RELATIONSHIP_FINDINGS", "_ARTIFACT_FINDINGS"):
        checks.update(getattr(audit, name))
    findings.update({name: connection.scalar(text(query)) for name, query in checks.items()})
    if any(findings.values()):
        raise BackupError(409, "backup_integrity_conflict",
                          "Restored data fails integrity checks or conflicts with other projects.")
