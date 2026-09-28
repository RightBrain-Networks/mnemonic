"""Native transcript searches spill to private temporary disk and clean up on close."""

from mnemonic_api.artifact_index import SearchDocument
from mnemonic_api.transcript_search_index import TranscriptSearchIndex


def test_native_index_uses_private_disk_and_removes_only_its_temporary_directory(tmp_path):
    sentinel = tmp_path / "unrelated"
    sentinel.write_text("keep")
    index = TranscriptSearchIndex()
    directory = index.directory
    assert directory is not None and directory.is_dir()
    assert directory.stat().st_mode & 0o777 == 0o700
    try:
        result = index.search("fixture", lambda: [SearchDocument("one", "", "needle")],
                              query="needle", fulltext=True, count=1)
        assert [hit.identity for hit in result.hits] == ["one"]
        assert list(directory.glob("snapshot/*/meta.json"))
    finally:
        index.close()
    assert not directory.exists()
    assert sentinel.read_text() == "keep"


def test_configured_index_survives_close_for_restart_reuse(tmp_path):
    index = TranscriptSearchIndex(tmp_path)
    assert index.directory == tmp_path
    index.start()
    index.close()
    assert tmp_path.is_dir()
