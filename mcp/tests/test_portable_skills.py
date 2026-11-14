"""Portable skills export only reachable resources and relocate independently."""

import ast
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


EXPECTED_RESOURCES = {
    "mnemonic-recall": {
        "reference/transcripts.md", "reference/repository-freshness.md",
        "reference/knowledge-graph.md", "bin/mnemonic-repository-freshness",
        "scripts/decision_provenance.py",
    },
    "mnemonic-save": {
        "reference/transcripts.md", "reference/knowledge-graph.md",
        "reference/artifact-transfers.md", "reference/artifacts.md",
        "scripts/upload_artifact.py", "scripts/download_artifact.py",
        "scripts/decision_provenance.py",
    },
    "mnemonic-search": {
        "reference/transcripts.md", "reference/artifacts.md", "scripts/download_artifact.py",
    },
}


def _assert_standalone_skill(skill: Path) -> None:
    entry = (skill / "SKILL.md").read_text()
    assert entry.startswith(f"---\nname: {skill.name}\n")
    assert len(entry.split("\n---\n", 1)[1].split()) <= 130
    assert {path.relative_to(skill).as_posix() for path in skill.rglob("*") if path.is_file()} == {
        "SKILL.md", *EXPECTED_RESOURCES[skill.name],
    }
    for document in skill.rglob("*.md"):
        content = document.read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" not in content
        assert str(PLUGIN_ROOT) not in content
        for link in re.findall(r"\[[^\]]+\]\(([^\s)]+)\)", content):
            if "://" in link or link.startswith("#"):
                continue
            target = (document.parent / link.split("#", 1)[0]).resolve()
            assert target.is_relative_to(skill.resolve()), (document, link)
            assert target.is_file(), (document, link)
    for relative in EXPECTED_RESOURCES[skill.name]:
        if relative.endswith(".md"):
            continue
        helper, source = skill / relative, PLUGIN_ROOT / relative
        assert helper.read_bytes() == source.read_bytes()
        assert stat.S_IMODE(helper.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
        if relative.startswith("bin/"):
            assert helper.stat().st_mode & stat.S_IXUSR
        if helper.suffix == ".py":
            ast.parse(helper.read_text(), feature_version=(3, 10))


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
    for name in SKILL_NAMES:
        skill = Path(shutil.move(str(original / name), str(relocated / name)))
        _assert_standalone_skill(skill)
        for relative in EXPECTED_RESOURCES[name]:
            if relative.endswith(".md"):
                continue
            arguments = [sys.executable, "--help"] if relative.endswith(".py") else []
            def invoke(root, arguments=arguments, relative=relative):
                command = ([arguments[0], str(root / relative), arguments[1]]
                           if arguments else [str(root / relative)])
                return subprocess.run(command, cwd=repository, capture_output=True,
                                      check=False, timeout=5)
            baseline, result = invoke(PLUGIN_ROOT), invoke(skill)
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


def test_exported_local_references_are_reachable_and_relocatable(exporter, tmp_path):
    exported = exporter.export_skills(tmp_path / "verified-transcript-skills")
    source = (PLUGIN_ROOT / "reference/transcripts.md").read_bytes()
    for name in SKILL_NAMES:
        assert (exported / name / "reference/transcripts.md").read_bytes() == source
    assert not (exported / "mnemonic-search/scripts/upload_artifact.py").exists()
    assert not (exported / "mnemonic-search/scripts/decision_provenance.py").exists()
    assert not (exported / "mnemonic-recall/scripts/download_artifact.py").exists()


def test_export_follows_local_links_once_and_omits_unreachable_files(exporter, tmp_path):
    plugin = tmp_path / "plugin"
    entry = plugin / "skills/example/SKILL.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("---\nname: example\ndescription: Example.\n---\n"
                     "[guide](../../reference/guide.md)\n")
    guide = plugin / "reference/guide.md"
    guide.parent.mkdir()
    guide.write_text("[self](guide.md#part) [helper](../scripts/helper.py)\n")
    helper = plugin / "scripts/helper.py"
    helper.parent.mkdir()
    helper.write_text("print('helper')\n")
    helper.chmod(0o755)
    (plugin / "reference/unused.md").write_text("not reachable\n")
    destination = exporter.export_skills(tmp_path / "export", plugin_root=plugin)
    skill = destination / "example"
    assert {path.relative_to(skill).as_posix() for path in skill.rglob("*") if path.is_file()} == {
        "SKILL.md", "reference/guide.md", "scripts/helper.py",
    }
    assert "[guide](reference/guide.md)" in (skill / "SKILL.md").read_text()
    assert "[self](guide.md#part)" in (skill / "reference/guide.md").read_text()
    assert (skill / "scripts/helper.py").stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize("resource", ["missing.md", "../../outside.md", "linked.md"])
def test_invalid_resource_fails_before_creating_export(exporter, tmp_path, resource):
    plugin = tmp_path / "plugin"
    entry = plugin / "skills/example/SKILL.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("---\nname: example\ndescription: Example.\n---\n"
                     f"[resource](${{CLAUDE_PLUGIN_ROOT}}/{resource})\n")
    outside = tmp_path / "outside.md"
    outside.write_text("private source\n")
    (plugin / "linked.md").symlink_to(outside)
    destination = tmp_path / "export"
    with pytest.raises(ValueError):
        exporter.export_skills(destination, plugin_root=plugin)
    assert not destination.exists()
    assert outside.read_text() == "private source\n"
