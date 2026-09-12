import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeLeaseSettings, leaseSettingsDraft, MAX_LEASE_MINUTES, parseLeaseDraft, validLeaseMinutes
} from "../lib/work-lease-settings.ts";
import { decodeProjectSettings } from "../lib/job-completion-reports.ts";
import { invalidMutationBody } from "../lib/proxy-policy.ts";

const projectId = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const settings = {
  project_id: projectId, revision: "1", recall_pointer_template: null,
  job_completion_report_prompt: "Write a concise summary.",
  code_review_required_min_priority: 100, code_review_optional_min_priority: 100,
  allow_remediation_code_reviews: false,
  lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120
};

test("lease duration drafts require ordered whole minutes and allow project limits above 120", () => {
  const draft = leaseSettingsDraft(settings);
  assert.deepEqual(parseLeaseDraft(draft), {
    lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120
  });
  assert.equal(parseLeaseDraft({ ...draft, lease_default_minutes: "240", lease_maximum_minutes: "360" }).lease_default_minutes, 240);
  for (const value of ["", "0", "-1", "1.5", "1e2", " 15", "15 ", String(MAX_LEASE_MINUTES + 1)]) {
    assert.equal(parseLeaseDraft({ ...draft, lease_minimum_minutes: value }), null);
  }
  assert.equal(parseLeaseDraft({ ...draft, lease_default_minutes: "9" }), null);
  assert.equal(parseLeaseDraft({ ...draft, lease_default_minutes: "121" }), null);
  assert.equal(parseLeaseDraft({ ...draft, lease_minimum_minutes: "15", lease_maximum_minutes: "15" }).lease_default_minutes, 15);
});

test("project and work reads require exact valid lease settings", () => {
  assert.deepEqual(decodeProjectSettings(settings, projectId), settings);
  const policy = { default_minutes: 15, minimum_minutes: 10, maximum_minutes: 120 };
  assert.deepEqual(decodeLeaseSettings(policy), policy);
  for (const value of [null, false, "15", 0, -1, 1.5, Infinity, MAX_LEASE_MINUTES + 1]) {
    assert.equal(validLeaseMinutes(value), false);
    assert.throws(() => decodeProjectSettings({ ...settings, lease_default_minutes: value }, projectId));
    assert.throws(() => decodeLeaseSettings({ ...policy, default_minutes: value }));
  }
  for (const change of [{ minimum_minutes: 16 }, { maximum_minutes: 14 }, { extra: true }, { default_minutes: undefined }]) {
    assert.throws(() => decodeLeaseSettings({ ...policy, ...change }));
  }
  assert.throws(() => decodeProjectSettings({ ...settings, lease_minimum_minutes: 16 }, projectId));
  assert.throws(() => decodeProjectSettings({ ...settings, lease_maximum_minutes: 14 }, projectId));
  assert.equal(validLeaseMinutes(MAX_LEASE_MINUTES), true);
});

test("browser settings patches permit revisioned lease edits and reject invalid numeric values", () => {
  const path = `projects/${projectId}/settings`;
  assert.equal(invalidMutationBody(path, "PATCH", { expected_revision: "1", ...parseLeaseDraft(leaseSettingsDraft(settings)) }), null);
  assert.equal(invalidMutationBody(path, "PATCH", { expected_revision: "1", lease_maximum_minutes: 360 }), null);
  for (const field of ["lease_default_minutes", "lease_minimum_minutes", "lease_maximum_minutes"]) {
    for (const value of [null, true, "15", 0, -1, 1.5, MAX_LEASE_MINUTES + 1]) {
      assert.ok(invalidMutationBody(path, "PATCH", { expected_revision: "1", [field]: value }));
    }
    assert.ok(invalidMutationBody(path, "PATCH", { [field]: 15 }));
  }
});
