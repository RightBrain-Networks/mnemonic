"""Private, bounded Markdown files with atomic updates."""

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.phase12_schemas import authoring_prompt

PROMPTS = {
    "recall-pointer": ("Recall pointer", "Start an assigned work session."),
    "job-completion-report": ("Job completion report", "Write a human-readable closeout report."),
    "cold-code-review": ("Cold code review", "Review pinned changes without authored context."),
    "warm-code-review": ("Warm code review", "Review changes with their retained context."),
    "review-recommendation": ("Review recommendation", "Ask whether completed work needs review."),
    "review-remediation": ("Review remediation", "Address all findings from a completed review."),
    "resume-work": ("Resume work", "Resume work with its current retained context."),
    "request-human-review": (
        "Request human review", "Write a decision for the human in Needs Attention.",
    ),
    "decision-impact-review": (
        "Decision impact review", "Investigate code affected by a changed decision document.",
    ),
}
MAX_BYTES = 400_000
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_REPLACED_DEFAULTS = {
    "decision-impact-review": {
        "db431809248375efccf70eb90e96d9e786ebb2eaaefbdb920de9e7b97221602f",
        "7405de8dae00a658cc978673bbcb361af95875398c0ad17006617360a9282e86",
        "dea47728c7926adc63863ed17138e6742270d321cfc85be76962c996f58e3935",
        "eb645d7711318d80f5f00fb0779d7cb3ae292afbeccb593143c739ed2b769e11",
        "946e52c89ad2ddc563207ed4b0bb542ea196db4a67cf13b63fa7b8b473631b97",
    },
}


def defaults_directory() -> Path:
    packaged = Path(__file__).parent / "prompts"
    return packaged if packaged.is_dir() else Path(__file__).resolve().parents[3] / "prompts"


def default_prompt(prompt_id: str) -> str:
    require_prompt(prompt_id)
    return (defaults_directory() / f"{prompt_id}.md").read_bytes().decode("utf-8")


def require_prompt(prompt_id: str) -> None:
    if prompt_id not in PROMPTS:
        raise not_found("prompt_not_found", "Prompt not found.")


def validate_prompt(prompt_id: str, content: str) -> str:
    require_prompt(prompt_id)
    if not content.strip() or len(content) > 100_000 or len(content.encode()) > MAX_BYTES:
        raise ValueError(
            "Prompts require nonblank text within 100,000 characters and 400,000 bytes"
        )
    if "\x00" in content:
        raise ValueError("Prompts cannot contain null bytes")
    if prompt_id == "review-recommendation":
        if len(content) > 4000 or len(content.encode()) > 8192:
            raise ValueError("Review recommendations cannot exceed 4,000 characters or 8,192 bytes")
        authoring_prompt(content)
    if prompt_id == "job-completion-report":
        if len(content) > 8000:
            raise ValueError("Report prompts cannot exceed 8,000 characters")
        authoring_prompt(content)
    return content


@dataclass(frozen=True)
class PromptFile:
    content: str
    revision: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime


def _regular(descriptor: int) -> os.stat_result:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
        raise OSError("Prompt storage requires private regular files owned by the API user")
    return info


class PromptStorage:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @contextmanager
    def _directory(self, *components: str) -> Iterator[int]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptors = [os.open(self.root, _DIRECTORY_FLAGS)]
        try:
            for component in components:
                if os.fstat(descriptors[-1]).st_uid != os.geteuid():
                    raise OSError("Prompt storage has an unexpected owner")
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptors[-1])
                    os.fsync(descriptors[-1])
                except FileExistsError:
                    pass
                descriptors.append(os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptors[-1]))
            if os.fstat(descriptors[-1]).st_uid != os.geteuid():
                raise OSError("Prompt storage has an unexpected owner")
            yield descriptors[-1]
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _read(directory: int, filename: str) -> tuple[bytes, os.stat_result]:
        descriptor = os.open(filename, _READ_FLAGS, dir_fd=directory)
        try:
            before = _regular(descriptor)
            if before.st_size > MAX_BYTES:
                raise OSError("Prompt exceeds the byte limit")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                content = stream.read(MAX_BYTES + 1)
            after = os.fstat(descriptor)
            if len(content) > MAX_BYTES or (before.st_mtime_ns, before.st_size) != (
                after.st_mtime_ns,
                after.st_size,
            ):
                raise OSError("Prompt changed during read")
            return content, after
        finally:
            os.close(descriptor)

    @staticmethod
    def _publish(directory: int, filename: str, content: bytes, *, exclusive: bool) -> None:
        temporary = ".pending-" + uuid4().hex
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(descriptor)
            if exclusive:
                os.link(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
            else:
                os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
                os.fsync(directory)
            except FileNotFoundError:
                pass

    def seed(self, project_id: UUID, prompt_id: str, content: str) -> PromptFile:
        validate_prompt(prompt_id, content)
        with self._directory("projects", str(UUID(str(project_id)))) as directory:
            try:
                self._publish(directory, f"{prompt_id}.md", content.encode(), exclusive=True)
            except FileExistsError:
                existing, _ = self._read(directory, f"{prompt_id}.md")
                if existing != content.encode():
                    raise OSError("Existing prompt differs from the migration export") from None
        return self.read(project_id, prompt_id)

    def read(self, project_id: UUID, prompt_id: str) -> PromptFile:
        require_prompt(prompt_id)
        try:
            current = self._read_prompt(project_id, prompt_id)
            return self._refresh_shipped_default(project_id, prompt_id, current)
        except OSError, UnicodeError, ValueError:
            raise ApplicationError(
                503, "prompt_unavailable", "Prompt content is unavailable."
            ) from None

    def _refresh_shipped_default(
        self, project_id: UUID, prompt_id: str, current: PromptFile,
    ) -> PromptFile:
        replaced = _REPLACED_DEFAULTS.get(prompt_id, set())
        if current.revision not in replaced:
            return current
        with self.lock(project_id):
            current = self._read_prompt(project_id, prompt_id)
            if current.revision not in replaced:
                return current
            content = validate_prompt(prompt_id, default_prompt(prompt_id))
            with self._directory("projects", str(UUID(str(project_id)))) as directory:
                self._publish(directory, f"{prompt_id}.md", content.encode(), exclusive=False)
            return self._read_prompt(project_id, prompt_id)

    def _read_prompt(self, project_id: UUID, prompt_id: str) -> PromptFile:
        with self._directory("projects", str(UUID(str(project_id)))) as directory:
            try:
                content, info = self._read(directory, f"{prompt_id}.md")
            except FileNotFoundError:
                try:
                    self._read(directory, f".{prompt_id}.created")
                except FileNotFoundError:
                    pass
                else:
                    raise OSError("A previously created prompt is missing") from None
                try:
                    self._publish(
                        directory,
                        f"{prompt_id}.md",
                        default_prompt(prompt_id).encode(),
                        exclusive=True,
                    )
                except FileExistsError:
                    pass
                content, info = self._read(directory, f"{prompt_id}.md")
            created_name = f".{prompt_id}.created"
            try:
                self._publish(directory, created_name, str(info.st_mtime).encode(), exclusive=True)
            except FileExistsError:
                pass
            created, _ = self._read(directory, created_name)
        text = validate_prompt(prompt_id, content.decode("utf-8"))
        return PromptFile(
            text,
            hashlib.sha256(content).hexdigest(),
            len(content),
            datetime.fromtimestamp(float(created), UTC),
            datetime.fromtimestamp(info.st_mtime, UTC),
        )

    @contextmanager
    def lock(self, project_id: UUID) -> Iterator[None]:
        with self._directory("projects", str(UUID(str(project_id)))) as directory:
            descriptor = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                _regular(descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                yield
            finally:
                os.close(descriptor)

    def write(self, project_id: UUID, prompt_id: str, content: str, revision: str) -> PromptFile:
        validate_prompt(prompt_id, content)
        try:
            with self.lock(project_id):
                # An explicit version-checked edit already holds the file lock.
                current = self._read_prompt(project_id, prompt_id)
                if current.revision != revision:
                    raise conflict("prompt_changed", "Prompt changed. Reload it before saving.")
                if current.content == content:
                    return current
                with self._directory("projects", str(UUID(str(project_id)))) as directory:
                    self._publish(directory, f"{prompt_id}.md", content.encode(), exclusive=False)
                return self._read_prompt(project_id, prompt_id)
        except OSError, UnicodeError, ValueError:
            raise ApplicationError(
                503, "prompt_unavailable", "Prompt could not be saved."
            ) from None
