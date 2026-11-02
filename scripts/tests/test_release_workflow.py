"""Check image availability and failure handling in the native release workflow."""

import itertools
import re
import unittest
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/images.yml"


def native_steps():
    native = WORKFLOW.read_text().split("\n  merge:", 1)[0]
    return dict(re.findall(
        r"^      - name: ([^\n]+)\n(.*?)(?=^      - name: |\Z)", native,
        re.MULTILINE | re.DOTALL,
    ))


class NativeImageWorkflowTest(unittest.TestCase):
    def test_only_cache_exports_ignore_errors(self):
        builds = [step for name, step in native_steps().items() if name.startswith("Build ")]
        self.assertEqual(len(builds), 4)
        for step in builds:
            cache = re.search(r"^          cache-to: (.+)$", step, re.MULTILINE)[1]
            self.assertIn("ignore-error=true", cache.split(","))
            # A failed build or registry push must still fail the job.
            self.assertNotIn("continue-on-error:", step)
            remaining = re.sub(r"^          cache-to: .+$", "", step, flags=re.MULTILINE)
            self.assertNotIn("ignore-error", remaining)

    def test_scans_require_available_images_even_after_another_step_fails(self):
        steps = native_steps()
        loader = steps["Load exact published digests for native checks"]
        loader_id = re.search(r"^        id: (.+)$", loader, re.MULTILINE)[1]
        outcomes = ("success", "failure", "skipped", "cancelled")
        scenarios = itertools.product((False, True), (False, True), outcomes, outcomes)
        for publish, cancelled, built, loaded in scenarios:
            expected = not cancelled and built == "success" and (
                not publish or loaded == "success"
            )
            for name in ("api", "mcp", "web", "tika"):
                with self.subTest(image=name, publish=publish, cancelled=cancelled,
                                  built=built, loaded=loaded):
                    scan = steps[f"Scan {name} for fixable critical vulnerabilities"]
                    condition = re.search(r"^        if: \$\{\{ (.+) \}\}$",
                                          scan, re.MULTILINE)[1]
                    # Evaluate the workflow's boolean expression with explicit step outcomes.
                    condition = condition.replace("cancelled()", repr(cancelled))
                    condition = condition.replace("inputs.publish", repr(publish))
                    condition = condition.replace(f"steps.{name}.outcome", repr(built))
                    condition = condition.replace(f"steps.{loader_id}.outcome", repr(loaded))
                    condition = condition.replace("!", "not ").replace("&&", " and ")
                    condition = condition.replace("||", " or ")
                    self.assertEqual(eval(condition, {"__builtins__": {}}), expected)
                    # Scans must still run after a sibling scan fails and fail on findings.
                    self.assertNotIn("success()", condition)
                    self.assertNotIn("continue-on-error:", scan)
                    self.assertIn("exit-code: '1'", scan)


if __name__ == "__main__":
    unittest.main()
