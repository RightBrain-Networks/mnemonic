"""Safe, actionable filesystem failures, without file bodies or OS exception text."""

import errno
import os
from pathlib import Path

from mnemonic_api.artifact_tika import ExtractionError

# These conditions can change outside the application. Recheck slowly even after
# the short transient retry budget, without asking users to rebuild ready text.
RECOVERABLE_COPY_ERRORS = (
    "transcript_source_missing", "transcript_permission_denied", "transcript_storage_full",
    "transcript_storage_read_only", "transcript_io_error", "transcript_copy_unavailable",
)
RECHECK_SECONDS = 300


class TranscriptAccessError(ExtractionError):
    def __init__(self, code: str, path: str, *, operation: str = "read_file",
                 retryable: bool = False, info: os.stat_result | None = None) -> None:
        super().__init__(code, retryable=retryable)
        self.details = {
            "path": path, "operation": operation, "uid": os.geteuid(), "gid": os.getegid(),
            "owner_uid": info.st_uid if info is not None else None,
            "owner_gid": info.st_gid if info is not None else None,
            "mode": f"{info.st_mode & 0o7777:04o}" if info is not None else None,
        }


def access_error(error: OSError, path: str, *, operation: str = "read_file",
                 info: os.stat_result | None = None) -> TranscriptAccessError:
    codes = {
        errno.ENOENT: "transcript_source_missing", errno.EACCES: "transcript_permission_denied",
        errno.EPERM: "transcript_permission_denied", errno.ELOOP: "transcript_symlink_rejected",
        errno.ENOTDIR: "transcript_not_directory", errno.EISDIR: "transcript_not_regular_file",
        errno.ENOSPC: "transcript_storage_full", errno.EDQUOT: "transcript_storage_full",
        errno.EROFS: "transcript_storage_read_only",
    }
    code = ("transcript_permission_denied" if isinstance(error, PermissionError)
            else codes.get(error.errno, "transcript_io_error"))
    return TranscriptAccessError(code, path, operation=operation, info=info,
                                 retryable=code == "transcript_io_error")


def permission_instruction(details: dict) -> str:
    operation = details.get("operation")
    permission = {
        "traverse": "search/execute (x) permission on this directory",
        "list_directory": "read and search/execute (r+x) permissions on this directory",
        "write_storage": "read, write and search/execute (rwx) permissions on private storage",
    }.get(operation, "read (r) permission on this file and search/execute (x) on its parents")
    identity = (f"The service UID {details.get('uid')} / GID {details.get('gid')} "
                f"needs {permission}. ")
    if details.get("uid") is not None and details.get("owner_uid") == details["uid"]:
        mode = "0700 (owner rwx) for this directory" if operation != "read_file" else (
            "0600 (owner rw) for this file and owner search/execute (x) on its parent directories")
        return (identity + f"Restore {mode}. If permissions already permit access, check host "
                "ACLs, security policy, container UID mappings and Docker file sharing.")
    return (identity + "For owner-only transcript files, run both API and worker with the source "
            "owner's numeric UID/GID (MNEMONIC_API_UID / MNEMONIC_API_GID), then rebuild and "
            "recreate both services. Keep transcript files private.")


def access_instruction(code: str, details: dict) -> str:
    if code == "transcript_source_missing" and details.get("operation") == "write_storage":
        return ("Restore the private transcript storage bind at this exact path in the worker. "
                "Create its host directory with mode 0700 owned by the worker UID/GID, then "
                "recreate the worker. Keep source mounts read-only.")
    if code == "transcript_permission_denied":
        return permission_instruction(details)
    instructions = {
        "transcript_source_missing": "Verify this exact path exists on the host and is mounted "
            "at the same absolute path in both API and worker. Restore a removed file, or use "
            "audited transcript path recovery for a verified native file in another directory.",
        "transcript_path_not_allowed": "Use a native transcript file within the configured "
            "source roots. If this is an intended source, configure its dedicated read-only "
            "mount and allowlist in both API and worker, then recreate both services. Do not "
            "allow temporary task output directories.",
        "transcript_not_regular_file": "Supply the exact native transcript file. A workflow "
            "directory can contain several agents and cannot be used as one transcript.",
        "transcript_symlink_rejected": "Supply and verify the actual native regular file "
            "beneath an approved root. Symlinks are not followed.",
        "transcript_not_directory": "A parent path is not a real directory. Verify the "
            "recorded path and its read-only mount; symlink directories are not supported.",
        "transcript_storage_full": "Free space or quota on the private transcript storage "
            "filesystem. Capture will resume automatically after space becomes available.",
        "transcript_storage_read_only": "Mount private transcript storage read-write for "
            "the worker. Source transcript mounts should remain read-only.",
        "transcript_copy_unavailable": "Check that private transcript storage is mounted "
            "and owned by the worker UID, with directories mode 0700 and files mode 0600.",
        "transcript_native_path_required": "Report a verified native .jsonl or .json file, "
            "not a workflow directory or temporary .output path. Use explicit null on a "
            "fresh request if the native file cannot be established.",
    }
    return instructions.get(code, "Check the source mount and private transcript storage. "
                            "Temporary I/O failures are retried automatically.")


def require_native_path(source: str) -> None:
    if Path(source).suffix.lower() not in {".jsonl", ".json"}:
        raise TranscriptAccessError("transcript_native_path_required", source)
