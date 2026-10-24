import assert from "node:assert/strict";
import test from "node:test";
import { findingSummaryLabel, validFindingMetadata, validFindingSummary } from "../lib/impact-findings.ts";

test("named findings and unlabelled rows retain distinct counts", () => {
  const value = { named_finding_count: 1, unlabelled_change_required_count: 2, change_required_row_count: 21 };
  assert.equal(validFindingSummary(value, 21), true);
  assert.equal(findingSummaryLabel(value), "1 named finding · 2 unlabelled rows");
  assert.equal(validFindingSummary(value, 20), false);
  assert.equal(validFindingSummary({ ...value, invented: 1 }), false);
  for (const change of [{ named_finding_count: 0 }, { named_finding_count: 20 }, { unlabelled_change_required_count: 22 }, { change_required_row_count: -1 }]) {
    assert.equal(validFindingSummary({ ...value, ...change }), false);
  }
});

test("finding metadata stays optional and targets require change-required", () => {
  assert.equal(validFindingMetadata({ disposition: "resolved" }), true);
  assert.equal(validFindingMetadata({ disposition: "change-required", finding_id: "wording:1", remediation_target: "decision-record" }), true);
  for (const finding_id of ["", "line\n", "a".repeat(101), "white space"]) {
    assert.equal(validFindingMetadata({ finding_id }), false);
  }
  assert.equal(validFindingMetadata({ disposition: "resolved", remediation_target: "code" }), false);
  for (const remediation_target of [["code"], { toString: () => "code" }, true, 1, "unknown"]) {
    assert.equal(validFindingMetadata({ disposition: "change-required", remediation_target }), false);
  }
});
