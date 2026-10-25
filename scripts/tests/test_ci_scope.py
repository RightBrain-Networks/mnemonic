"""Exercise CI selection against real Git histories without app dependencies."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ci_scope.py"
SPEC = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)


class DocumentationPathsTest(unittest.TestCase):
    def test_project_docs(self):
        for path in ("README.md", "AGENTS.md", "AGENT-README.md", "docs/development.md",
                     "docs/nested/guide.md", "docs/images/nested/screenshot.png"):
            with self.subTest(path=path):
                self.assertTrue(ci_scope.is_documentation(path))

    def test_runtime_and_unknown_paths_require_full_ci(self):
        for path in ("backend/src/app.py", "frontend/app.tsx", "mcp/uv.lock",
                     "prompts/job-completion-report.md", "plugin/skills/recall/SKILL.md",
                     "backend/src/mnemonic_api/priority_rubric.md", "docs/openapi.json",
                     "docs/validation-vocabulary.json", "docs/check.py", "docs/script.sh",
                     ".github/workflows/ci.yml", "scripts/ci_scope.py", "other.md",
                     "docs/../app.md", "/docs/guide.md", "docs/images/script.js"):
            with self.subTest(path=path):
                self.assertFalse(ci_scope.is_documentation(path))


class EventDiffTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "CI test")
        self.git("config", "user.email", "ci-test@example.invalid")
        self.write("README.md", "Initial docs\n")
        self.write("backend/app.py", "print('initial')\n")
        self.base = self.commit()

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", *arguments], cwd=self.repository, stderr=subprocess.PIPE, text=True,
        ).strip()

    def write(self, path, content):
        destination = self.repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-m", "Test fixture")
        return self.git("rev-parse", "HEAD")

    def required(self, event_name="push", *, base=None, head=None):
        base = base or self.base
        head = head or self.git("rev-parse", "HEAD")
        event = ({"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
                 if event_name == "pull_request" else {"before": base, "after": head})
        return ci_scope.requires_full_ci(event_name, event, self.repository)

    def test_docs_only_push_and_pr(self):
        self.write("docs/a guide\nwith spaces.md", "Documentation\n")
        self.commit()
        self.assertFalse(self.required())
        self.assertFalse(self.required("pull_request"))

    def test_earlier_code_commit_is_not_hidden_by_latest_docs_commit(self):
        self.write("backend/app.py", "print('changed')\n")
        self.commit()
        self.write("README.md", "Updated docs\n")
        self.commit()
        self.assertTrue(self.required())
        self.assertTrue(self.required("pull_request"))

    def test_pr_uses_merge_base_when_main_advances(self):
        self.git("checkout", "-b", "docs")
        self.write("README.md", "Updated docs\n")
        head = self.commit()
        self.git("checkout", "main")
        self.write("backend/app.py", "print('new main')\n")
        base = self.commit()
        self.assertFalse(self.required("pull_request", base=base, head=head))
        self.assertTrue(self.required("push", base=base, head=head))

    def test_code_renamed_into_docs_still_requires_full_ci(self):
        (self.repository / "docs").mkdir()
        self.git("mv", "backend/app.py", "docs/guide.md")
        self.commit()
        self.assertTrue(self.required())

    def test_docs_renamed_into_code_requires_full_ci(self):
        self.git("mv", "README.md", "backend/new.py")
        self.commit()
        self.assertTrue(self.required())

    def test_deleting_docs_can_skip_but_deleting_code_cannot(self):
        (self.repository / "README.md").unlink()
        docs_head = self.commit()
        self.assertFalse(self.required())
        (self.repository / "backend/app.py").unlink()
        self.commit()
        self.assertTrue(self.required(base=docs_head))

    def test_manual_missing_unknown_and_empty_diffs_run_full_ci(self):
        self.assertTrue(self.required())
        self.write("README.md", "Updated docs\n")
        self.commit()
        self.assertTrue(self.required("workflow_dispatch"))
        self.assertTrue(self.required(base="0" * 40))
        self.assertTrue(self.required(base="f" * 40))
        self.assertTrue(ci_scope.requires_full_ci("pull_request", {}, self.repository))

    def test_cli_appends_github_output(self):
        self.write("README.md", "Updated docs\n")
        head = self.commit()
        event_file = self.repository / "event.json"
        event_file.write_text(json.dumps({"before": self.base, "after": head}))
        output = self.repository / "output"
        output.write_text("existing=value\n")
        subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=self.repository, check=True,
            env={**os.environ, "GITHUB_EVENT_NAME": "push",
                 "GITHUB_EVENT_PATH": str(event_file), "GITHUB_OUTPUT": str(output)},
            capture_output=True,
        )
        self.assertEqual(output.read_text(), "existing=value\nfull_ci=false\n")


if __name__ == "__main__":
    unittest.main()
