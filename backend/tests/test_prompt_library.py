"""Filesystem safety and literal macro expansion do not require PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.prompt_storage import PROMPTS, PromptStorage, default_prompt
from mnemonic_api.services.prompts import expand_macros


def test_catalog_has_one_markdown_file_per_prompt():
    assert len(PROMPTS) == 9
    for prompt_id in PROMPTS:
        assert default_prompt(prompt_id).strip()


def test_macro_expansion_is_literal_single_pass_and_respects_token_boundaries():
    values = {"$PROJECT_ID": "actual-id", "$WORK_ITEM_TITLE": "$PROJECT_ID $$ $&"}
    assert expand_macros(
        "$WORK_ITEM_TITLE / $PROJECT_ID / $PROJECT_ID_extra / $UNKNOWN", values
    ) == ("$PROJECT_ID $$ $& / actual-id / $PROJECT_ID_extra / $UNKNOWN")


def test_storage_preserves_bytes_created_time_and_detects_external_edits(tmp_path):
    storage = PromptStorage(tmp_path)
    project = uuid4()
    first = storage.read(project, "recall-pointer")
    text = "  Exact Unicode 🧭\r\n$PROJECT_ID\n"
    saved = storage.write(project, "recall-pointer", text, first.revision)
    assert saved.content == text
    assert saved.size_bytes == len(text.encode())
    assert saved.created_at == first.created_at
    path = tmp_path / "projects" / str(project) / "recall-pointer.md"
    path.write_text("External editor change", encoding="utf-8")
    edited = storage.read(project, "recall-pointer")
    assert edited.revision != saved.revision
    assert edited.created_at == first.created_at
    with pytest.raises(ApplicationError) as error:
        storage.write(project, "recall-pointer", "stale overwrite", saved.revision)
    assert error.value.detail["code"] == "prompt_changed"
    assert storage.read(uuid4(), "recall-pointer").content == first.content


@pytest.mark.parametrize("unsafe", ["symlink", "hardlink", "fifo", "deleted"])
def test_storage_rejects_unsafe_or_missing_registered_files(tmp_path, unsafe):
    storage = PromptStorage(tmp_path / "library")
    project = uuid4()
    storage.read(project, "recall-pointer")
    path = storage.root / "projects" / str(project) / "recall-pointer.md"
    path.unlink()
    outside = tmp_path / "outside.md"
    outside.write_text("Private outside text")
    if unsafe == "symlink":
        path.symlink_to(outside)
    elif unsafe == "hardlink":
        os.link(outside, path)
    elif unsafe == "fifo":
        os.mkfifo(path)
    with pytest.raises(ApplicationError) as error:
        storage.read(project, "recall-pointer")
    assert error.value.detail["code"] == "prompt_unavailable"
    assert outside.read_text() == "Private outside text"


def test_concurrent_saves_cannot_both_replace_same_revision(tmp_path):
    storage = PromptStorage(tmp_path)
    project = uuid4()
    initial = storage.read(project, "recall-pointer")

    def save(text):
        try:
            return storage.write(project, "recall-pointer", text, initial.revision).content
        except ApplicationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["First writer", "Second writer"]))
    assert len([result for result in results if result is not None]) == 1
    assert storage.read(project, "recall-pointer").content in results


def test_special_prompt_bounds_and_invalid_paths(tmp_path):
    storage = PromptStorage(tmp_path)
    with pytest.raises(ValueError):
        storage.seed(uuid4(), "job-completion-report", "x" * 8001)
    with pytest.raises(ValueError):
        storage.seed(uuid4(), "review-recommendation", "🧭" * 2100)
    with pytest.raises(ApplicationError) as error:
        storage.read(uuid4(), "../../outside")
    assert error.value.detail["code"] == "prompt_not_found"


def test_storage_rejects_non_uuid_project_paths(tmp_path):
    storage = PromptStorage(tmp_path / "library")
    with pytest.raises(ApplicationError):
        storage.read("../../outside", "recall-pointer")
    assert not (tmp_path / "outside").exists()


def _old_impact_prompt(version="v1"):
    return (Path(__file__).parent / "fixtures"
            / f"decision-impact-review-{version}.md").read_bytes()


def _install_impact_prompt(storage, project, content):
    original = storage.read(project, "decision-impact-review")
    path = storage.root / "projects" / str(project) / "decision-impact-review.md"
    path.write_bytes(content)
    return original, path


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_impact_default_upgrade_preserves_creation_time_and_is_stable(tmp_path, version):
    storage = PromptStorage(tmp_path)
    project = uuid4()
    original, path = _install_impact_prompt(storage, project, _old_impact_prompt(version))
    before = storage._read_prompt(project, "decision-impact-review")
    refreshed = storage.read(project, "decision-impact-review")
    assert refreshed.content == default_prompt("decision-impact-review")
    assert refreshed.revision != before.revision
    assert refreshed.created_at == original.created_at
    assert path.read_bytes() == refreshed.content.encode()
    assert storage.read(project, "decision-impact-review") == refreshed
    with pytest.raises(ApplicationError) as error:
        storage.write(project, "decision-impact-review", "A stale edit", before.revision)
    assert error.value.detail["code"] == "prompt_changed"


@pytest.mark.parametrize("content", [b"My review instructions", _old_impact_prompt() + b"\n",
                                     _old_impact_prompt("v2") + b"\n",
                                     _old_impact_prompt("v3") + b"\n"])
def test_impact_default_upgrade_preserves_customized_bytes(tmp_path, content):
    storage = PromptStorage(tmp_path)
    project = uuid4()
    original, path = _install_impact_prompt(storage, project, content)
    current = storage.read(project, "decision-impact-review")
    assert current.content.encode() == content
    assert path.read_bytes() == content
    assert current.created_at == original.created_at


def test_impact_upgrade_rereads_under_lock_before_replacing(tmp_path, monkeypatch):
    from contextlib import contextmanager

    storage = PromptStorage(tmp_path)
    project = uuid4()
    _, path = _install_impact_prompt(storage, project, _old_impact_prompt())
    lock = storage.lock

    @contextmanager
    def edited_before_lock(project_id):
        path.write_bytes(b"A concurrent human edit")
        with lock(project_id):
            yield

    monkeypatch.setattr(storage, "lock", edited_before_lock)
    assert storage.read(project, "decision-impact-review").content == "A concurrent human edit"
    assert path.read_bytes() == b"A concurrent human edit"


def test_impact_default_upgrade_can_retry_after_publish_failure(tmp_path, monkeypatch):
    storage = PromptStorage(tmp_path)
    project = uuid4()
    _, path = _install_impact_prompt(storage, project, _old_impact_prompt())
    publish = storage._publish

    def failed_publish(directory, filename, content, *, exclusive):
        if filename == "decision-impact-review.md" and not exclusive:
            raise OSError("Injected replacement failure")
        return publish(directory, filename, content, exclusive=exclusive)

    with monkeypatch.context() as failing:
        failing.setattr(storage, "_publish", failed_publish)
        with pytest.raises(ApplicationError) as error:
            storage.read(project, "decision-impact-review")
        assert error.value.detail["code"] == "prompt_unavailable"
    assert path.read_bytes() == _old_impact_prompt()
    assert storage.read(project, "decision-impact-review").content == default_prompt(
        "decision-impact-review"
    )
