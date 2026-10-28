"""Regression checks for release drift, bump safety and interrupted generators."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "release.py"
SPEC = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.versions = release.current_versions(release.ROOT)
        self.app = self.versions["app"]
        for relative, _, _ in release.FIELDS:
            self.write(relative, (release.ROOT / relative).read_text())
        self.write(release.PLUGIN_MANIFEST, json.dumps({"version": self.versions["plugin"]}))
        self.write("frontend/package.json", json.dumps({"version": self.app}))
        self.write("compose.yaml", f"image: example/api:${{MNEMONIC_VERSION:-{self.app}}}\n")
        self.write(".env.example", f"MNEMONIC_VERSION={self.app}\n")
        self.write("docs/history.md", f"Release {self.app} introduced an earlier feature.\n")
        self.generated(self.root)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def generated(self, root: Path) -> None:
        app = release.current_versions(root)["app"]
        for package, name in (("backend", "mnemonic-api"), ("mcp", "mnemonic-mcp")):
            self.write(f"{package}/uv.lock", f'[[package]]\nname = "{name}"\nversion = "{app}"\n')
        self.write("frontend/package-lock.json", json.dumps({
            "version": app, "packages": {"": {"version": app}},
        }))
        self.write("docs/openapi.json", json.dumps({"info": {"version": app}}))

    def next_patch(self) -> str:
        major, minor, patch_number = release.version_tuple(self.app)
        return f"{major}.{minor}.{patch_number + 1}"

    def test_check_rejects_mismatched_tag_and_versions(self) -> None:
        self.assertEqual(release.check(self.root, f"v{self.app}"), self.versions)
        with self.assertRaisesRegex(ValueError, "Tag"):
            release.check(self.root, "v999.0.0")
        for relative in ("frontend/package.json", "frontend/package-lock.json",
                         "backend/uv.lock", "mcp/uv.lock", "docs/openapi.json",
                         "AGENTS.md", "mcp/src/mnemonic_mcp/__init__.py",
                         "compose.yaml", ".env.example"):
            path = self.root / relative
            original = path.read_text()
            with self.subTest(path=relative):
                path.write_text(original.replace(self.app, "999.0.0"))
                with self.assertRaisesRegex(ValueError, "Release versions disagree"):
                    release.check(self.root)
                path.write_text(original)

    def test_missing_current_declaration_fails_instead_of_silently_skipping(self) -> None:
        self.write("AGENTS.md", "Instructions without a current release.\n")
        with self.assertRaisesRegex(ValueError, "exactly one current-version declaration"):
            release.check(self.root)

    def test_bump_updates_all_authored_surfaces_and_preserves_history(self) -> None:
        history = (self.root / "docs/history.md").read_bytes()
        with patch.object(release, "regenerate", side_effect=self.generated):
            result = release.bump(self.root, self.next_patch(), None)
        self.assertEqual(result, {"app": self.next_patch(), "plugin": self.versions["plugin"]})
        self.assertEqual((self.root / "docs/history.md").read_bytes(), history)
        self.assertIn(self.next_patch(), (self.root / "compose.yaml").read_text())

    def test_plugin_can_bump_independently(self) -> None:
        major, minor, patch_number = release.version_tuple(self.versions["plugin"])
        plugin = f"{major}.{minor}.{patch_number + 1}"
        with patch.object(release, "regenerate", side_effect=self.generated):
            self.assertEqual(release.bump(self.root, self.app, plugin), {
                "app": self.app, "plugin": plugin,
            })

    def test_invalid_or_unapproved_version_does_not_modify_files(self) -> None:
        old = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        major = release.version_tuple(self.app)[0]
        for version in ("01.2.3", "1.2", "1.2.3-rc.1", "0.0.0", f"{major + 1}.0.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.bump(self.root, version, None)
            self.assertEqual({path: path.read_bytes() for path in old}, old)

    def test_interrupted_generator_can_resume_the_same_bump(self) -> None:
        target = self.next_patch()
        failure = subprocess.CalledProcessError(2, "uv")
        with patch.object(release, "regenerate", side_effect=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                release.bump(self.root, target, None)
        with self.assertRaisesRegex(ValueError, "Release versions disagree"):
            release.check(self.root)
        with patch.object(release, "regenerate", side_effect=self.generated):
            self.assertEqual(release.bump(self.root, target, None)["app"], target)


if __name__ == "__main__":
    unittest.main()
