"""The shipped management command verifies before applying and defaults to dry-run."""

import importlib.util
import json
from pathlib import Path

import pytest

from .test_transcript_reclaim_postgres import file_inventory, legacy_snapshots

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('arguments', [[], ['--dry-run'], ['--apply']])
def test_reclaim_cli_defaults_to_nonmutating_dry_run(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch, capsys, arguments,
):
    storage, _ = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    before = file_inventory(storage.root)
    script = Path(__file__).parents[2] / 'scripts/reclaim_transcript_copies.py'
    spec = importlib.util.spec_from_file_location('test_reclaim_command', script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'Settings', lambda: api.app.state.settings)
    monkeypatch.setattr('sys.argv', [str(script), *arguments])
    assert module.main() == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]['verified_snapshots'] == 4
    assert lines[0]['estimated_reclaimable_bytes'] > 0
    if arguments == ['--apply']:
        assert lines[-1]['legacy_files_reclaimed'] == 4
        assert lines[-1]['verification_after']['rows_to_reclaim'] == 0
    else:
        assert lines[0]['mode'] == 'dry-run'
        assert file_inventory(storage.root) == before
