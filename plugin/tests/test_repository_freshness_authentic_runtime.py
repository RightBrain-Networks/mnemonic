from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BASH_ENVIRONMENT = "MNEMONIC_AUTHENTIC_BASH"
GIT_ENVIRONMENT = "MNEMONIC_AUTHENTIC_GIT"
EXACT_BASH_ENVIRONMENT = "MNEMONIC_REQUIRE_EXACT_BASH_3_2"


def resolve_executable(value: str) -> Path:
    candidate = shutil.which(value)
    if candidate is None:
        raise AssertionError(f"executable is not available: {value!r}")
    resolved = Path(candidate).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise AssertionError(f"executable is not an executable file: {resolved}")
    return resolved


def invoke(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=check,
        capture_output=True,
        timeout=60,
    )


def parse_protocol(stdout: bytes) -> tuple[dict[str, str], list[str]]:
    text = stdout.decode("ascii")
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise AssertionError(f"invalid protocol framing: {text!r}")
    fields: dict[str, str] = {}
    paths: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise AssertionError(f"invalid protocol line: {line!r}")
        if key == "path_byte_q":
            paths.append(value)
        elif key == "detail":
            continue
        else:
            if key in fields:
                raise AssertionError(f"duplicate protocol field: {key}")
            fields[key] = value
    return fields, paths


class AuthenticRepositoryFreshnessRuntimeTests(unittest.TestCase):
    def test_installed_payload_with_real_bash_and_git(self) -> None:
        bash_setting = os.environ.get(BASH_ENVIRONMENT)
        git_setting = os.environ.get(GIT_ENVIRONMENT)
        exact_bash_setting = os.environ.get(EXACT_BASH_ENVIRONMENT, "0")
        if bash_setting is None and git_setting is None and exact_bash_setting == "0":
            self.skipTest(
                f"set {BASH_ENVIRONMENT} and {GIT_ENVIRONMENT} to enable authentic runtime coverage"
            )
        self.assertIsNotNone(bash_setting, f"{BASH_ENVIRONMENT} must be set")
        self.assertIsNotNone(git_setting, f"{GIT_ENVIRONMENT} must be set")
        self.assertIn(exact_bash_setting, {"0", "1"})

        bash = resolve_executable(bash_setting or "")
        git = resolve_executable(git_setting or "")
        base_environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
            and key not in {"BASH_ENV", "ENV", "GLOBIGNORE", "PS4", "BASH_XTRACEFD"}
        }
        base_environment.update(
            {
                "LC_ALL": "C",
                "LANG": "C",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_SYSTEM": "/dev/null",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_ATTR_NOSYSTEM": "1",
                "PATH": f"{git.parent}:/usr/bin:/bin:/usr/sbin:/sbin",
            }
        )
        selected_git = shutil.which("git", path=base_environment["PATH"])
        self.assertIsNotNone(selected_git)
        self.assertEqual(Path(selected_git or "").resolve(strict=True), git)

        bash_version = invoke(
            [
                str(bash),
                "--noprofile",
                "--norc",
                "-c",
                (
                    'printf "%s\\n" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}" '
                    '"${BASH_VERSINFO[2]}"'
                ),
            ],
            cwd=PLUGIN_ROOT,
            environment=base_environment,
        )
        bash_parts = tuple(int(part) for part in bash_version.stdout.splitlines())
        self.assertEqual(len(bash_parts), 3, bash_version.stdout)
        self.assertGreaterEqual(bash_parts[:2], (3, 2))
        if exact_bash_setting == "1":
            self.assertEqual(bash_parts[:2], (3, 2))

        git_version = invoke(
            [str(git), "--version"],
            cwd=PLUGIN_ROOT,
            environment=base_environment,
        )
        match = re.fullmatch(
            rb"git version ([0-9]+)\.([0-9]+)(?:\.([0-9]+))?(?: .*)?\n",
            git_version.stdout,
        )
        self.assertIsNotNone(match, git_version.stdout)
        assert match is not None
        git_parts = tuple(int(part) if part is not None else 0 for part in match.groups())
        self.assertGreaterEqual(git_parts[:2], (2, 45))

        manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory(prefix="mnemonic-authentic-runtime-") as temporary:
            root = Path(temporary)
            installed_plugin = (
                root
                / "config"
                / "plugins"
                / "cache"
                / "mnemonic"
                / "mnemonic"
                / manifest["version"]
            )
            shutil.copytree(
                PLUGIN_ROOT,
                installed_plugin,
                copy_function=shutil.copy2,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            helper = installed_plugin / "bin" / "mnemonic-repository-freshness"
            self.assertTrue(helper.stat().st_mode & stat.S_IXUSR)
            self.assertNotEqual(helper, PLUGIN_ROOT / "bin" / helper.name)
            self.assertEqual(helper.read_bytes(), (PLUGIN_ROOT / "bin" / helper.name).read_bytes())

            repository = root / "repository"
            repository.mkdir()
            invoke([str(git), "init", "-q"], cwd=repository, environment=base_environment)
            invoke(
                [str(git), "config", "user.name", "Authentic Runtime Test"],
                cwd=repository,
                environment=base_environment,
            )
            invoke(
                [str(git), "config", "user.email", "runtime@example.invalid"],
                cwd=repository,
                environment=base_environment,
            )
            tracked = repository / "tracked.txt"
            tracked.write_bytes(b"baseline\n")
            invoke(
                [str(git), "add", "--", "tracked.txt"],
                cwd=repository,
                environment=base_environment,
            )
            invoke(
                [str(git), "-c", "commit.gpgSign=false", "commit", "-q", "-m", "baseline"],
                cwd=repository,
                environment=base_environment,
            )
            baseline = invoke(
                [str(git), "rev-parse", "HEAD"],
                cwd=repository,
                environment=base_environment,
            ).stdout.decode("ascii").strip()
            self.assertRegex(baseline, r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")

            command = [
                str(bash),
                "--noprofile",
                "--norc",
                "-p",
                str(helper),
                "--baseline",
                baseline,
                "--path",
                "tracked.txt",
            ]
            clean = invoke(
                command,
                cwd=repository,
                environment=base_environment,
                check=False,
            )
            self.assertEqual(clean.stderr, b"")
            self.assertEqual(clean.returncode, 0, clean.stdout)
            clean_fields, clean_paths = parse_protocol(clean.stdout)
            self.assertEqual(clean_fields["protocol"], "mnemonic-repository-freshness-v1")
            self.assertEqual(clean_fields["state"], "unchanged")
            self.assertEqual(clean_fields["reason"], "no_relevant_change_observed")
            self.assertEqual(clean_fields["baseline_oid"], baseline)
            self.assertEqual(clean_fields["head_oid"], baseline)
            self.assertEqual(clean_fields["pattern_count"], "1")
            self.assertEqual(clean_fields["matched_pattern_count"], "1")
            self.assertEqual(clean_fields["displayed_path_count"], "0")
            self.assertEqual(clean_paths, [])

            tracked.write_bytes(b"dirty\n")
            dirty = invoke(
                command,
                cwd=repository,
                environment=base_environment,
                check=False,
            )
            self.assertEqual(dirty.stderr, b"")
            self.assertEqual(dirty.returncode, 10, dirty.stdout)
            dirty_fields, dirty_paths = parse_protocol(dirty.stdout)
            self.assertEqual(dirty_fields["protocol"], "mnemonic-repository-freshness-v1")
            self.assertEqual(dirty_fields["state"], "changed")
            self.assertEqual(dirty_fields["reason"], "relevant_change_observed")
            self.assertEqual(dirty_fields["baseline_oid"], baseline)
            self.assertEqual(dirty_fields["head_oid"], baseline)
            self.assertEqual(dirty_fields["pattern_count"], "1")
            self.assertEqual(dirty_fields["matched_pattern_count"], "1")
            self.assertEqual(dirty_fields["displayed_path_count"], "1")
            self.assertEqual(dirty_paths, ["tracked.txt"])


if __name__ == "__main__":
    unittest.main()
