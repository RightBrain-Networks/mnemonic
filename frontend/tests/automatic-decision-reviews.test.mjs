import assert from "node:assert/strict";
import test from "node:test";
import { parseDecisionReviewPriority, validDecisionReviewPriority } from "../lib/automatic-decision-reviews.ts";
import { decodeProjectSettings } from "../lib/job-completion-reports.ts";
import { decodeWorkItem } from "../lib/work-codecs.ts";
import { decodeTaskPage } from "../lib/tasks.ts";
import { PROMPT_IDS, promptQueryKeys } from "../lib/prompts.ts";
import * as fixtures from "./phase12-fixtures.mjs";

const settings = {
  project_id: fixtures.project, revision: "1", recall_pointer_template: null,
  job_completion_report_prompt: "Describe the outcome.",
  lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120,
  code_review_required_min_priority: 100, code_review_optional_min_priority: 100,
  allow_remediation_code_reviews: false, automatic_decision_reviews: false, decision_review_priority: 40
};

test("decision impact review settings enforce a boolean and an integer priority from zero through 100", () => {
  assert.deepEqual(decodeProjectSettings(settings, fixtures.project), settings);
  for (const value of [0, 1, 40, 99, 100]) {
    assert.equal(validDecisionReviewPriority(value), true);
    assert.equal(parseDecisionReviewPriority(String(value)), value);
    assert.equal(decodeProjectSettings({ ...settings, decision_review_priority: value }, fixtures.project).decision_review_priority, value);
  }
  for (const value of [null, undefined, false, true, "40", -1, 101, 40.5, NaN, Infinity]) {
    assert.equal(validDecisionReviewPriority(value), false);
    assert.throws(() => decodeProjectSettings({ ...settings, decision_review_priority: value }, fixtures.project));
  }
  for (const value of ["", "-1", "101", "40.5", "1e2", " 40", "40 ", "Infinity"]) {
    assert.equal(parseDecisionReviewPriority(value), null);
  }
  for (const value of [null, undefined, "false", 0, 1]) {
    assert.throws(() => decodeProjectSettings({ ...settings, automatic_decision_reviews: value }, fixtures.project));
  }
});

test("decision impact review origin survives work and task decoding without changing their ordinary lifecycle", () => {
  const work = { ...fixtures.workItem, decision_review_revision_id: fixtures.project };
  assert.equal(decodeWorkItem(work, fixtures.project).decision_review_revision_id, fixtures.project);
  for (const value of ["not-a-uuid", false, 4, {}]) {
    assert.throws(() => decodeWorkItem({ ...work, decision_review_revision_id: value }, fixtures.project));
  }
  const item = { id: fixtures.work, work_item_id: fixtures.work, project_id: fixtures.project,
    kind: "work_item", work_version: 1, title: "Review a changed decision", summary: "Investigate downstream code.",
    status: "pending", review_state: null, updated_at: fixtures.timestamp, lease: null,
    decision_review_revision_id: fixtures.project };
  const page = { project_id: fixtures.project, work_items: { active: 0, pending: 1 },
    code_reviews: { active: 0, pending: 0 }, next_lease_expires_at: null,
    items: [item], total: 1, limit: 20, offset: 0 };
  assert.deepEqual(decodeTaskPage(page, fixtures.project, "pending", "work_item", 0), page);
  assert.throws(() => decodeTaskPage({ ...page, items: [{ ...item, decision_review_revision_id: "invalid" }] }, fixtures.project, "pending", "work_item", 0));
  assert.throws(() => decodeTaskPage({ ...page, items: [{ ...item, kind: "code_review", review_state: "requested" }] }, fixtures.project, "pending", "code_review", 0));
});

test("decision impact review instructions belong to the managed prompt catalog", () => {
  assert.ok(PROMPT_IDS.includes("decision-impact-review"));
  const route = `projects/${fixtures.project}/prompts/decision-impact-review`;
  assert.deepEqual(promptQueryKeys(route, "GET"), []);
  assert.deepEqual(promptQueryKeys(route, "PUT"), []);
  assert.deepEqual(promptQueryKeys(`${route}/render`, "POST"), []);
});
