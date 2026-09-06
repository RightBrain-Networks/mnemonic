"""Portable skill exports retain native workflows without client substitutions."""

import importlib.util
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin"
EXPORTER_PATH = REPOSITORY_ROOT / "scripts" / "export_agent_skills.py"
SKILL_NAMES = {"mnemonic-save", "mnemonic-search", "mnemonic-recall"}


@pytest.fixture
def exporter():
    spec = importlib.util.spec_from_file_location("mnemonic_skill_exporter", EXPORTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_standalone_skill(skill: Path) -> None:
    assert (skill / "SKILL.md").read_text().startswith(f"---\nname: {skill.name}\n")
    references = {path.name for path in (PLUGIN_ROOT / "reference").glob("*.md")}
    assert {path.name for path in (skill / "reference").iterdir()} == references
    assert {path.relative_to(skill).as_posix() for path in skill.rglob("*") if path.is_file()} == {
        "SKILL.md", "bin/mnemonic-repository-freshness",
        *(f"reference/{name}" for name in references),
    }
    for document in (skill / "SKILL.md", *(skill / "reference").glob("*.md")):
        content = document.read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in content
        assert str(PLUGIN_ROOT) not in content
        for link in re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", content):
            if "://" in link or link.startswith("#"):
                continue
            target = (document.parent / link.split("#", 1)[0]).resolve()
            assert target.is_relative_to(skill.resolve()), (document, link)
            assert target.is_file(), (document, link)
    helper = skill / "bin" / "mnemonic-repository-freshness"
    source_helper = PLUGIN_ROOT / "bin" / helper.name
    assert helper.read_bytes() == source_helper.read_bytes()
    assert stat.S_IMODE(helper.stat().st_mode) == stat.S_IMODE(source_helper.stat().st_mode)
    assert helper.stat().st_mode & stat.S_IXUSR


def test_cli_exports_three_complete_skills_without_mutating_source(tmp_path):
    destination = tmp_path / "portable skills"
    source_before = {
        path: path.read_bytes() for path in PLUGIN_ROOT.rglob("*.md")
    }
    result = subprocess.run(
        [sys.executable, str(EXPORTER_PATH), str(destination)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert {path.name for path in destination.iterdir()} == SKILL_NAMES
    for skill in destination.iterdir():
        _assert_standalone_skill(skill)
    assert all(path.read_bytes() == content for path, content in source_before.items())


def test_each_skill_can_relocate_alone_and_run_helper_outside_bundle(exporter, tmp_path):
    original = exporter.export_skills(tmp_path / "original")
    relocated = tmp_path / "different client with spaces"
    relocated.mkdir()
    repository = tmp_path / "repository working directory"
    repository.mkdir()
    baseline = subprocess.run(
        [str(PLUGIN_ROOT / "bin" / "mnemonic-repository-freshness")],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=5,
    )
    for name in SKILL_NAMES:
        skill = Path(shutil.move(str(original / name), str(relocated / name)))
        _assert_standalone_skill(skill)
        result = subprocess.run(
            [str(skill / "bin" / "mnemonic-repository-freshness")],
            cwd=repository,
            capture_output=True,
            check=False,
            timeout=5,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            baseline.returncode, baseline.stdout, baseline.stderr,
        )
    assert not list(original.iterdir())
    assert not list(repository.iterdir())


@pytest.mark.parametrize("existing_kind", ["file", "directory", "symlink", "dangling_symlink"])
def test_export_refuses_existing_destination_without_changing_it(exporter, tmp_path, existing_kind):
    destination = tmp_path / "existing"
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserve this")
    if existing_kind == "file":
        destination.write_text("original file")
    elif existing_kind == "directory":
        destination.mkdir()
        (destination / "user-skill").write_text("original skill")
    else:
        destination.symlink_to(sentinel if existing_kind == "symlink" else tmp_path / "missing")
    with pytest.raises((FileExistsError, ValueError)):
        exporter.export_skills(destination)
    assert sentinel.read_text() == "preserve this"
    if existing_kind == "file":
        assert destination.read_text() == "original file"
    elif existing_kind == "directory":
        assert [path.name for path in destination.iterdir()] == ["user-skill"]
        assert (destination / "user-skill").read_text() == "original skill"
    else:
        assert destination.is_symlink()
    assert not (tmp_path / "missing").exists()


def test_export_refuses_symlinked_parent_and_missing_parent(exporter, tmp_path):
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        exporter.export_skills(linked_parent / "skills")
    with pytest.raises(ValueError, match="symlinks"):
        exporter.export_skills(linked_parent / ".." / "skills")
    with pytest.raises(ValueError, match="must already exist"):
        exporter.export_skills(tmp_path / "missing" / "skills")
    assert not list(real_parent.iterdir())
    assert not (tmp_path / "missing").exists()


def test_cli_requires_destination_and_reports_refusal(tmp_path):
    for arguments in ([], [str(tmp_path)]):
        result = subprocess.run(
            [sys.executable, str(EXPORTER_PATH), *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        assert result.returncode != 0
        assert result.stdout == ""
    assert not list(tmp_path.iterdir())
