"""Locally authored storage remedies, without rendering upstream diagnostics."""

_STORAGE_FAILURES = {
    "storage_owner_mismatch": (
        "Artifact storage ownership does not match the API service user. Ask an operator to "
        "repair ownership within MNEMONIC_ARTIFACT_ROOT for the API service UID/GID."
    ),
    "storage_permission_denied": (
        "Artifact storage permission denied. Ask an operator to grant the API service user "
        "read, write and directory traversal access to MNEMONIC_ARTIFACT_ROOT."
    ),
    "storage_full": (
        "Artifact storage is full or its quota is exhausted. Ask an operator to restore free "
        "space and quota for MNEMONIC_ARTIFACT_ROOT."
    ),
    "storage_read_only": (
        "Artifact storage is read-only. Ask an operator to restore a writable mount for "
        "MNEMONIC_ARTIFACT_ROOT."
    ),
    "storage_integrity": (
        "Artifact storage failed an integrity check. Ask an operator to inspect and repair "
        "MNEMONIC_ARTIFACT_ROOT before resuming artifact operations."
    ),
    "storage_unavailable": (
        "Artifact storage is unavailable. Ask an operator to inspect and repair "
        "MNEMONIC_ARTIFACT_ROOT and its storage mount."
    ),
}
_THIS_ATTEMPT_NOT_COMMITTED = (
    " This attempt did not commit an artifact mutation; it does not resolve any earlier or "
    "concurrent attempt with the same operation UUID."
)
_UNCERTAIN_OPERATION = (
    " The mutation outcome remains uncertain; a durable intent or receipt may already exist "
    "for this operation UUID."
)
_PRESERVE_OPERATION = (
    " Preserve the original client_operation_id (operation UUID), exact arguments and bytes. "
    "Do not generate a replacement UUID for the same intent."
)


def artifact_storage_message(
    status_code: int,
    application_error: tuple[str, dict[str, object]] | None,
    *,
    safe_read: bool,
    receipt_protected_write: bool,
) -> str | None:
    """Classify only the controlled storage contract; all other failures fall through."""
    if (
        status_code != 503
        or application_error is None
        or application_error[0] != "artifact_storage_unavailable"
    ):
        return None
    context = application_error[1]
    cause = context.get("cause")
    # Validate the wire type before looking up a potentially unhashable upstream value.
    if not isinstance(cause, str) or cause not in _STORAGE_FAILURES:
        return None
    message = _STORAGE_FAILURES[cause] + " Stop automatic retries pending operator repair."
    if safe_read:
        return message
    # Literal true describes staging in this invocation, never the UUID's entire history.
    if receipt_protected_write and context.get("attempt_not_committed") is True:
        message += _THIS_ATTEMPT_NOT_COMMITTED
    else:
        message += _UNCERTAIN_OPERATION
    return message + _PRESERVE_OPERATION
