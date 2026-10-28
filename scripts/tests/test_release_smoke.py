"""The release runner must never adopt production storage or inherited settings."""

import importlib.util
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release_smoke", Path(__file__).resolve().parents[1] / "release_smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class ReleaseSmokeTests(unittest.TestCase):
    def test_inherited_production_configuration_is_removed(self):
        with (patch.dict(os.environ, {"COMPOSE_FILE": "production.yaml",
                                      "MNEMONIC_API_KEY": "production-secret",
                                      "POSTGRES_PASSWORD": "production-password"}),
              patch.object(smoke, "free_port", side_effect=[19001, 19002, 19003])):
            stack = smoke.Stack(Path("/tmp/disposable"))
        self.assertNotIn("COMPOSE_FILE", stack.environment)
        self.assertNotIn("MNEMONIC_API_KEY", stack.environment)
        self.assertNotIn("POSTGRES_PASSWORD", stack.environment)

    def test_existing_or_external_volumes_are_refused(self):
        for volume in ({"name": "mnemonic_data"},
                       {"name": "test_data", "external": True},
                       {"name": "test_data", "driver_opts": {"device": "/production"}}):
            with self.subTest(volume=volume), self.assertRaises(RuntimeError):
                smoke.require_isolation({"volumes": {"data": volume}, "services": {}},
                                        "test", Path("/tmp/bundle"))

    def test_writable_or_outside_binds_are_refused(self):
        for mount in ({"type": "bind", "source": "/tmp/bundle/source"},
                      {"type": "bind", "source": "/production", "read_only": True}):
            with self.subTest(mount=mount), self.assertRaises(RuntimeError):
                smoke.require_isolation({"services": {"api": {"volumes": [mount]}}},
                                        "test", Path("/tmp/bundle"))

    def test_archive_cannot_escape_extraction_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo("../outside")
                entry.size = 1
                output.addfile(entry, io.BytesIO(b"x"))
            with self.assertRaises(tarfile.FilterError):
                smoke.unpack(archive, root / "extracted")
            self.assertFalse((root / "outside").exists())


if __name__ == "__main__":
    unittest.main()
