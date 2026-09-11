"""Import scans cannot escape operator roots or silently exceed bounded coverage."""

import os

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_discovery import discover_transcripts


def test_scans_nested_jsonl_and_skips_links_and_nonregular_files(tmp_path):
    root = tmp_path / "allowed"
    root.mkdir()
    child = root / "session" / "subagents"
    child.mkdir(parents=True)
    (child / "agent.jsonl").write_text("{}")
    (root / "main.jsonl").write_text("{}")
    (root / "sessions-index.json").write_text("{}")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.jsonl").write_text("{}")
    (root / "linked").symlink_to(outside, target_is_directory=True)
    (root / "alias.jsonl").symlink_to(root / "main.jsonl")
    os.mkfifo(root / "pipe.jsonl")
    scan = discover_transcripts(str(root), [root])
    assert scan.paths == sorted([str(root / "main.jsonl"), str(child / "agent.jsonl")])
    assert scan.skipped == 3


@pytest.mark.parametrize("mode", ["outside", "parent", "symlink", "root_symlink"])
def test_rejects_unapproved_or_symlink_directories(tmp_path, mode):
    root = tmp_path / "root"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    directory, roots = {
        "outside": (str(tmp_path), [root]),
        "parent": (str(root / ".."), [root]),
        "symlink": (str(alias), [tmp_path]),
        "root_symlink": (str(alias), [alias]),
    }[mode]
    with pytest.raises(ApplicationError):
        discover_transcripts(directory, roots)


@pytest.mark.parametrize("limit,value", [("MAX_IMPORT_FILES", 1), ("MAX_IMPORT_ENTRIES", 1),
                                         ("MAX_IMPORT_DEPTH", 0), ("SCAN_SECONDS", -1)])
def test_rejects_partial_scans_instead_of_silently_importing_a_prefix(tmp_path, monkeypatch,
                                                                  limit, value):
    (tmp_path / "a.jsonl").write_text("{}")
    child = tmp_path / "nested"
    child.mkdir()
    (child / "b.jsonl").write_text("{}")
    monkeypatch.setattr("mnemonic_api.transcript_discovery." + limit, value)
    with pytest.raises(ApplicationError) as failure:
        discover_transcripts(str(tmp_path), [tmp_path])
    assert failure.value.detail["code"] == "transcript_import_scan_limit"
