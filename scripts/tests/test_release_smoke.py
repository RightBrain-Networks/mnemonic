"""The release runner must never adopt production storage or inherited settings."""

import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "release_smoke", Path(__file__).resolve().parents[1] / "release_smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class ReleaseSmokeTests(unittest.TestCase):
    def test_setup_uses_exact_container_image_and_caller_identity(self):
        choices = [("0.116.0", False, "ghcr.io/rightbrain-networks/mnemonic-api:0.116.0"),
                   ("sha-candidate-1-2", False,
                    "ghcr.io/rightbrain-networks/mnemonic-api:sha-candidate-1-2"),
                   ("0.116.1", True, "mnemonic-api:local")]
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(smoke, "free_port", side_effect=[19001, 19002, 19003])):
            root = Path(directory)
            (root / "setup.py").write_text("# release installer\n")
            stack = smoke.Stack(root)
            for tag, local, image in choices:
                with self.subTest(image=image), patch.object(smoke.subprocess, "run") as run:
                    run.side_effect = lambda *args, **kwargs: smoke.private_write(
                        root / ".env", "MNEMONIC_API_KEY=disposable-key\n")
                    stack.setup(root, tag, local)
                    command = run.call_args.args[0]
                    self.assertEqual(command[:3], ["docker", "run", "--rm"])
                    self.assertEqual(command[command.index("--user") + 1],
                                     f"{os.getuid()}:{os.getgid()}")
                    self.assertEqual(command[command.index("--pull") + 1],
                                     "never" if local else "always")
                    self.assertEqual(command[-4:], ["--entrypoint", "python", image,
                                                    "/release/setup.py"])
                    self.assertNotIn("disposable-key", " ".join(command))

    def test_setup_refuses_nonprivate_settings_before_compose(self):
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(smoke, "free_port", side_effect=[19001, 19002, 19003])):
            root = Path(directory)
            (root / "setup.py").write_text("# release installer\n")
            (root / ".env").write_text("MNEMONIC_API_KEY=disposable-key\n")
            (root / ".env").chmod(0o644)
            with patch.object(smoke.subprocess, "run"), self.assertRaisesRegex(
                    RuntimeError, "caller-owned regular 0600"):
                smoke.Stack(root).setup(root, "0.116.1", True)

    def test_upgrade_supplies_existing_settings_and_refuses_installer_changes(self):
        settings = "MNEMONIC_API_KEY=original-disposable-key\n"
        config = {"services": {name: {} for name in ("api", "mcp", "web")}}
        with (tempfile.TemporaryDirectory() as directory,
              patch.object(smoke, "free_port", side_effect=[19001, 19002, 19003])):
            root = Path(directory)
            stack = smoke.Stack(root)
            for changed in (False, True):
                def setup(path, tag, local, changed=changed):
                    self.assertEqual((path / ".env").read_text(), settings)
                    self.assertEqual((tag, local), ("sha-candidate-1-2", False))
                    if changed:
                        smoke.private_write(path / ".env", "MNEMONIC_API_KEY=replaced-key\n")

                with (self.subTest(changed=changed), patch.object(stack, "setup", setup),
                      patch.object(smoke.subprocess, "run", return_value=SimpleNamespace(
                          stdout=json.dumps(config))) as run):
                    if changed:
                        with self.assertRaisesRegex(RuntimeError, "changed the existing"):
                            stack.configure(root, "sha-candidate-1-2", False, settings)
                        run.assert_not_called()
                    else:
                        self.assertEqual(stack.configure(root, "sha-candidate-1-2", False,
                                                         settings), settings)
                        run.assert_called_once()

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
