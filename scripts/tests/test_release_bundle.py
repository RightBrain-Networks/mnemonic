"""Exercise install archive boundaries and release metadata against real Git fixtures."""

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "release_bundle", Path(__file__).resolve().parents[1] / "release_bundle.py",
)
release_bundle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_bundle)


class ReleaseBundleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "source"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Release test")
        self.git("config", "user.email", "release@example.invalid")
        for name in release_bundle.INSTALL_FILES:
            self.write(name, "fixture\n")
        self.write("backend/pyproject.toml", '[project]\nversion="0.116.1"\n')
        self.write(".env.example", "POSTGRES_PASSWORD=\nTIMEZONE=UTC\n")
        self.write("plugin/.claude-plugin/plugin.json", '{"version":"0.71.0"}')
        self.write(".claude-plugin/marketplace.json", '{"plugins":[{"name":"mnemonic"}]}')
        self.write("backend/alembic/versions/0001_first.py",
                   '"""Initial schema."""\nrevision="0001"\ndown_revision=None\n')
        self.write("deploy/empty-transcripts/.keep", "")
        self.write("deploy/nginx/mnemonic.conf", "server {}\n")
        self.write("deploy/nginx/test/secret.txt", "test-only\n")
        self.write("docs/backup-recovery.md", "Recovery instructions\n")
        self.write("docs/images/not-a-guide.md", "Image metadata\n")
        self.git("add", ".")
        self.git("commit", "-m", "Fixture")
        self.git("tag", "v0.116.0")
        self.write(".env", "POSTGRES_PASSWORD=do-not-publish\n")
        self.write("deploy/nginx/untracked-secret", "do-not-publish\n")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_bundle_is_reproducible_and_excludes_untracked_or_test_content(self):
        out = self.root.parent / "out"
        release_bundle.build(self.root, out)
        first = (out / "mnemonic-0.116.1.tar.gz").read_bytes()
        release_bundle.build(self.root, out)
        self.assertEqual(first, (out / "mnemonic-0.116.1.tar.gz").read_bytes())
        with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
            files = {p.name.removeprefix("mnemonic-0.116.1/"):
                     archive.extractfile(p).read() for p in archive.getmembers()}
        self.assertIn("deploy/empty-transcripts/.keep", files)
        self.assertIn("setup.py", files)
        self.assertIn("scripts/setup.py", files)
        self.assertIn("AGENTS.md", files)
        self.assertIn("README.md", files)
        self.assertIn("AGENT-README.md", files)
        self.assertIn("docs/upgrade-history.md", files)
        self.assertIn("docs/backup-recovery.md", files)
        self.assertNotIn("docs/images/not-a-guide.md", files)
        self.assertNotIn(".env", files)
        self.assertNotIn("deploy/nginx/untracked-secret", files)
        self.assertNotIn("deploy/nginx/test/secret.txt", files)
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(digest, hashlib.sha256(files[name]).hexdigest())
        catalog = json.loads(files["marketplace.json"])
        source = catalog["plugins"][0]["source"]
        self.assertEqual(source["ref"], "v0.116.1")
        self.assertEqual(source["sha"], self.git("rev-parse", "HEAD"))
        self.assertEqual(source["path"], "plugin")
        self.assertIn(b"no previous published-image upgrade", files["UPGRADE.md"])

    def test_upgrade_notes_list_only_new_migrations_and_configuration_changes(self):
        self.write("backend/alembic/versions/0002_second.py",
                   '"""New feature."""\nrevision: str="0002"\n'
                   'down_revision: str | None="0001"\n')
        self.write(".env.example", "POSTGRES_PASSWORD=\nTIMEZONE=Europe/London\n")
        notes = release_bundle.upgrade_notes(self.root, "0.116.1", "0.71.0", "v0.116.0")
        self.assertIn("head before: `0001`", notes)
        self.assertIn("head after: `0002`", notes)
        self.assertIn("`0002`: New feature.", notes)
        self.assertNotIn("`0001`: Initial schema.", notes)
        self.assertIn("`TIMEZONE`", notes)
        self.assertNotIn("`POSTGRES_PASSWORD`", notes)

    def test_missing_release_file_fails_instead_of_shipping_incomplete_bundle(self):
        (self.root / "compose.bind-mounts.yaml").unlink()
        with self.assertRaises(FileNotFoundError):
            release_bundle.build(self.root, self.root.parent / "out")

    def test_multiple_schema_heads_are_refused(self):
        self.write("backend/alembic/versions/0002_orphan.py",
                   'revision="0002"\ndown_revision=None\n')
        with self.assertRaisesRegex(ValueError, "exactly one"):
            release_bundle.upgrade_notes(self.root, "0.116.1", "0.71.0", None)


class MigrationCatalogTest(unittest.TestCase):
    def test_annotated_merge_metadata_uses_literal_values(self):
        self.assertEqual(release_bundle.migration_metadata(
            '"""Merge branches."""\nrevision: str = "0003"\n'
            'down_revision: tuple[str, str] = ("0001", "0002")\n'),
            ("0003", ("0001", "0002"), "Merge branches."))
        with self.assertRaises(ValueError):
            release_bundle.migration_metadata(
                'revision: str = str("0001")\ndown_revision = None\n')

    def test_shipped_migrations_match_the_documented_alembic_head(self):
        root = Path(__file__).resolve().parents[2]
        catalog = release_bundle.migrations(root)
        documented = re.search(r"Alembic head `([^`]+)`", (root / "AGENTS.md").read_text())
        self.assertIsNotNone(documented)
        self.assertEqual(release_bundle.head(catalog), documented[1])
        self.assertEqual(catalog["0020_project_activity.py"][:2],
                         ("0020_project_activity", ("0019_structured_completion_evidence",)))


if __name__ == "__main__":
    unittest.main()
