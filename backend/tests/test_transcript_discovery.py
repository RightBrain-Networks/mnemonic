"""Import scans cannot escape operator roots or silently exceed bounded coverage."""

import io
import json
import os

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_discovery import discover_transcripts


@pytest.mark.parametrize("prefix", ["", "/", "//"])
def test_scans_nested_jsonl_and_skips_links_and_nonregular_files(tmp_path, prefix):
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
    scan = discover_transcripts(prefix + str(root), [root])
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


@pytest.mark.parametrize("record_type", ["session_meta", "response_item", "turn_context",
                                         "event_msg", "compacted"])
def test_selects_clients_from_native_records_without_filename_or_nested_text_inference(
    tmp_path, record_type,
):
    codex = tmp_path / "claude-subagent.jsonl"
    codex.write_text("\ufeff\n" + json.dumps({"type": record_type, "payload": {"id": "native"}}))
    claude = tmp_path / "rollout-codex.jsonl"
    claude.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "session_meta", "payload": {"id": "text-only"}},
    ]}}))
    scan = discover_transcripts(str(tmp_path), [tmp_path])
    assert scan.clients == {str(codex): "codex", str(claude): "claude_code"}
    assert scan.skipped == 0


@pytest.mark.parametrize("content", [b"not json", b"\xff", b"[]", b'{"type": []}',
                                      b'{"type":"session_meta","payload":"invalid"}'])
def test_unknown_or_invalid_sources_remain_import_candidates(tmp_path, content):
    path = tmp_path / "broken.jsonl"
    path.write_bytes(content)
    scan = discover_transcripts(str(tmp_path), [tmp_path])
    assert scan.paths == [str(path)]
    assert scan.clients == {str(path): "claude_code"}
    assert scan.skipped == 0


@pytest.mark.parametrize("limit,value", [("MAX_SIGNATURE_BYTES", 20),
                                         ("MAX_SIGNATURE_RECORDS", 1)])
def test_signature_read_has_byte_and_record_bounds(tmp_path, monkeypatch, limit, value):
    import mnemonic_api.transcript_detection as detection

    prefix = b'{"unrelated": "header"}\n'
    signature = b'{"type":"session_meta","payload":{"id":"later"}}\n'
    content = io.BytesIO(prefix + signature)
    monkeypatch.setattr(detection, limit, value)
    assert detection.detect_transcript_client(content) == "claude_code"
    assert content.tell() <= (value + 1 if limit == "MAX_SIGNATURE_BYTES" else len(prefix))
    path = tmp_path / "large.jsonl"
    path.write_bytes(prefix + signature)
    scan = discover_transcripts(str(tmp_path), [tmp_path])
    assert scan.paths == [str(path)]
    assert scan.skipped == 0


@pytest.mark.parametrize("replacement", ["symlink", "fifo"])
def test_signature_read_rejects_files_replaced_by_links_or_pipes(
    tmp_path, monkeypatch, replacement,
):
    root = tmp_path / "root"
    root.mkdir()
    path = root / "session.jsonl"
    path.write_text("{}")
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"type":"session_meta","payload":{"id":"outside"}}')
    original_open = os.open

    def replace_source(name, flags, *args, **kwargs):
        if name == path.name:
            path.unlink()
            if replacement == "symlink":
                path.symlink_to(outside)
            else:
                os.mkfifo(path)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr("mnemonic_api.transcript_discovery.os.open", replace_source)
    scan = discover_transcripts(str(root), [root])
    assert scan.paths == [str(path)]
    assert scan.clients == {str(path): "claude_code"}
    assert scan.skipped == 0
