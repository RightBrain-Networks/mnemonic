import assert from "node:assert/strict";
import test from "node:test";
import { workSummaryMaxChars, workSummaryValidationMessage } from "../lib/work-summary-limit.ts";

test("work summary configuration has a 2048 default and accepts positive integers", () => {
  assert.equal(workSummaryMaxChars(undefined), 2048);
  assert.equal(workSummaryMaxChars("4096"), 4096);
  assert.equal(workSummaryMaxChars("32"), 32);
  for (const value of ["", "0", "-1", "2.5", "text", "Infinity", "9007199254740992"]) {
    assert.throws(() => workSummaryMaxChars(value), /positive integer/);
  }
});

test("summary validation counts Unicode characters and reports the selected maximum", () => {
  for (const maximum of [32, 2048, 4096]) {
    assert.equal(workSummaryValidationMessage("🧠".repeat(maximum), maximum), "");
    assert.equal(workSummaryValidationMessage(`  ${"é".repeat(maximum)}\n`, maximum), "");
    assert.equal(workSummaryValidationMessage("🧠".repeat(maximum + 1), maximum),
      `Work summary exceeds the configured maximum of ${maximum} characters.`);
  }
});
