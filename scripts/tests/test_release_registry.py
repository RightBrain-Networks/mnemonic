"""Release gates reject incomplete or mismatched published-image evidence."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_registry", SCRIPTS / "release_registry.py")
registry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(registry)
SPEC = importlib.util.spec_from_file_location("release_github", SCRIPTS / "release_github.py")
github = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(github)
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


def manifest(digest=DIGEST):
    return {"digest": digest, "manifests": [
        {"digest": "sha256:" + "d" * 64,
         "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:" + "e" * 64,
         "platform": {"os": "linux", "architecture": "arm64"}},
        {"annotations": {"vnd.docker.reference.type": "attestation-manifest",
                         "vnd.docker.reference.digest": "sha256:" + "d" * 64}},
        {"annotations": {"vnd.docker.reference.type": "attestation-manifest",
                         "vnd.docker.reference.digest": "sha256:" + "e" * 64}},
    ]}


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.value = {"commit": COMMIT, "version": "0.116.1",
                      "candidate_tag": f"sha-{COMMIT}-1234-1",
                      "images": dict.fromkeys(registry.IMAGES, DIGEST)}
        for arch in ("amd64", "arm64"):
            (self.root / f"{arch}.json").write_text(json.dumps(
                {**self.value, "platform": f"linux/{arch}"},
            ))

    def test_two_native_platforms_required(self):
        self.assertEqual(set(registry.load_native(self.root, COMMIT, "0.116.1")),
                         {"linux/amd64", "linux/arm64"})
        (self.root / "arm64.json").unlink()
        with self.assertRaises(ValueError):
            registry.load_native(self.root, COMMIT, "0.116.1")

    def test_mixed_commits_and_missing_images_refused(self):
        for change in ({"commit": "c" * 40}, {"images": {"api": DIGEST}}):
            (self.root / "arm64.json").write_text(json.dumps(
                {**self.value, "platform": "linux/arm64", **change},
            ))
            with self.assertRaises(ValueError):
                registry.load_native(self.root, COMMIT, "0.116.1")

    def test_missing_platform_or_attestations_refused(self):
        self.assertEqual(registry.verify_index(manifest()), DIGEST)
        for index in (0, 1, 2, 3):
            document = manifest()
            document["manifests"].pop(index)
            with self.assertRaises(ValueError):
                registry.verify_index(document)

    def test_retry_uses_candidate_receipt_original_attempt(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps(self.value))
        output = self.root / "output"
        with patch.dict(registry.os.environ, {"GITHUB_RUN_ATTEMPT": "2",
                                              "GITHUB_OUTPUT": str(output)}), \
                patch("builtins.print") as report:
            registry.candidate(receipt, COMMIT, "0.116.1")
        self.assertEqual(output.read_text(), f"tag=sha-{COMMIT}-1234-1\n")
        report.assert_called_once_with(f"sha-{COMMIT}-1234-1")

    def test_receipt_tag_must_match_release_commit(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({**self.value, "candidate_tag": "sha-" + "c" * 40 + "-1-1"}))
        with self.assertRaisesRegex(ValueError, "original build"):
            registry.load_candidate(receipt, COMMIT, "0.116.1")

    def test_existing_release_digest_cannot_be_overwritten(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps(self.value))
        result = subprocess.CompletedProcess([], 0, json.dumps(manifest("sha256:" + "c" * 64)))
        with patch.object(registry, "inspect", return_value=manifest()), \
                patch.object(registry.subprocess, "run", return_value=result), \
                patch.object(registry, "run") as publish:
            with self.assertRaisesRegex(ValueError, "overwrite"):
                registry.promote(receipt, COMMIT, "0.116.1")
            publish.assert_not_called()

    def test_registry_failure_does_not_mean_release_tag_absent(self):
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps(self.value))
        result = subprocess.CompletedProcess([], 1, "", "connection timed out")
        with patch.object(registry, "inspect", return_value=manifest()), \
                patch.object(registry.subprocess, "run", return_value=result), \
                patch.object(registry, "run") as publish:
            with self.assertRaises(RuntimeError):
                registry.promote(receipt, COMMIT, "0.116.1")
            publish.assert_not_called()


class GitHubReleaseTest(unittest.TestCase):
    def test_first_release_has_explicit_empty_baseline(self):
        self.assertEqual(github.previous([], "v0.116.1", True), "")

    def test_baseline_is_semantically_sorted(self):
        releases = [{"tag_name": tag} for tag in ("v0.9.0", "v0.100.1", "v0.99.1")]
        self.assertEqual(github.previous(releases, "v0.116.1", True), "v0.100.1")
        with self.assertRaises(ValueError):
            github.previous(releases, "v0.99.2", True)

    def test_nightly_can_test_current_release(self):
        releases = [{"tag_name": "v0.116.1"}]
        self.assertEqual(github.previous(releases, "v0.116.1", False), "v0.116.1")
        with self.assertRaises(ValueError):
            github.previous(releases, "v0.116.1", True)

    def test_required_check_must_match_commit_and_latest_run(self):
        good = {"id": 1, "name": "Required checks", "head_sha": COMMIT,
                "conclusion": "success", "app": {"slug": "github-actions"}}
        with patch.object(github, "gh", return_value=json.dumps([{"check_runs": [good]}])):
            github.verify_ci(COMMIT)
        pending = {**good, "id": 2, "conclusion": None}
        with patch.object(github, "gh", return_value=json.dumps([
            {"check_runs": [good, pending]},
        ])), self.assertRaises(ValueError):
            github.verify_ci(COMMIT)


if __name__ == "__main__":
    unittest.main()
