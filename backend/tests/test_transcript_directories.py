"""The folder picker exposes directories within approved source boundaries only."""

import pytest

from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_browse import browse_directories


def test_browse_lists_roots_directories_and_bounded_parent(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "b").mkdir()
    (root / "A").mkdir()
    (root / "file.jsonl").write_text("{}")
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    assert browse_directories(None, [root]).entries[0].path == str(root)
    listing = browse_directories(str(root), [root])
    assert listing.parent is None and not listing.truncated
    assert [entry.name for entry in listing.entries] == ["A", "b"]
    assert browse_directories(str(root / "A"), [root]).parent == str(root)


@pytest.mark.parametrize("suffix", ["/../", "/link", "/missing"])
def test_browse_refuses_traversal_symlinks_and_missing_directories(tmp_path, suffix):
    root = tmp_path / "source"
    root.mkdir()
    (root / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ApplicationError):
        browse_directories(str(root) + suffix, [root])
    with pytest.raises(ApplicationError):
        browse_directories(str(tmp_path), [root])


def test_browse_excludes_retained_storage_and_reports_limit(tmp_path, monkeypatch):
    (tmp_path / "private").mkdir()
    (tmp_path / "sessions").mkdir()
    result = browse_directories(str(tmp_path), [tmp_path], (tmp_path / "private",))
    assert [entry.name for entry in result.entries] == ["sessions"]
    with pytest.raises(ApplicationError):
        browse_directories(str(tmp_path / "private"), [tmp_path], (tmp_path / "private",))
    monkeypatch.setattr("mnemonic_api.transcript_browse.MAX_BROWSE_ENTRIES", 1)
    assert browse_directories(str(tmp_path), [tmp_path]).truncated


def test_browse_filters_before_result_limit(tmp_path, monkeypatch):
    for name in ("a", "b", "needle"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr("mnemonic_api.transcript_browse.MAX_BROWSE_ENTRIES", 1)
    result = browse_directories(str(tmp_path), [tmp_path], query="NEEDLE")
    assert [row.name for row in result.entries] == ["needle"]
    assert not result.truncated
