import assert from "node:assert/strict";
import test from "node:test";

import {
  availableStatusActions,
  currentManualStatusAction,
  decodeDashboardActivationResult,
  decodeLeaseReleaseResult,
  humanDecisionCompletionCheckpoint,
  humanDecisionReport,
  statusActionDisabledReason
} from "../lib/work-status-actions.ts";

const work = {
  id: "7a5dc555-0a6d-4f92-9678-1647524827c8",
  project_id: "e36a7e53-938f-4c8a-b75a-af9c7331711a",
  title: "Choose the lifecycle",
  summary: "Manual decision fixture.",
  status: "pending",
  priority: 50,
  initial_checkpoint_id: "26a3a437-0af3-405a-ab82-7932d17869e0",
  version: 1,
  created_at: "2026-09-01T12:00:00Z",
  updated_at: "2026-09-01T12:00:00Z"
};

function readiness(overrides = {}) {
  return {
    lifecycle_status: "pending",
    is_duplicate: false,
    canonical_work_item_id: work.id,
    is_terminal: false,
    has_active_lease: false,
    has_dropped_lease: false,
    active_lease: null,
    unresolved_blocker_count: 0,
    is_blocked: false,
    unresolved_gate_count: 0,
    is_gated: false,
    is_ready: true,
    display_state: "pending",
    ...overrides
  };
}

test("the status menu has the requested order and excludes the current state", () => {
  assert.deepEqual(
    availableStatusActions("pending", readiness()).map((item) => item.label),
    ["Active", "Done", "Won’t Do", "Promote"]
  );
  const active = readiness({
    has_active_lease: true,
    active_lease: {
      holder_client: "dashboard",
      holder_session_id: "tab",
      acquired_at: "2026-09-01T12:00:00Z",
      renewed_at: "2026-09-01T12:00:00Z",
      expires_at: "2026-09-01T12:15:00Z"
    },
    is_ready: false,
    display_state: "active"
  });
  assert.equal(currentManualStatusAction("pending", active), "active");
  assert.deepEqual(
    availableStatusActions("pending", active).map((item) => item.label),
    ["Pending", "Done", "Won’t Do", "Promote"]
  );
  const dropped = readiness({
    has_dropped_lease: true,
    is_ready: false,
    display_state: "dropped"
  });
  assert.equal(currentManualStatusAction("pending", dropped), null);
  assert.deepEqual(
    availableStatusActions("pending", dropped).map((item) => item.label),
    ["Pending", "Active", "Done", "Won’t Do", "Promote"]
  );
  assert.deepEqual(
    availableStatusActions("done", readiness({
      lifecycle_status: "done",
      is_terminal: true,
      is_ready: false,
      display_state: "done"
    })).map((item) => item.label),
    ["Pending", "Active", "Won’t Do", "Promote"]
  );
  assert.equal(currentManualStatusAction("deferred", readiness({
    lifecycle_status: "deferred",
    is_ready: false,
    display_state: "deferred"
  })), "defer");
});

test("terminal actions wait for gates and report settings while Active also respects blockers", () => {
  const gated = readiness({ unresolved_gate_count: 1, is_gated: true, is_ready: false });
  assert.match(
    statusActionDisabledReason("done", readiness({
      unresolved_blocker_count: 1,
      is_blocked: true,
      is_ready: false
    }), true),
    /incoming blocker/
  );
  assert.match(statusActionDisabledReason("done", gated, true), /human question/);
  assert.match(statusActionDisabledReason("active", gated, true), /human question/);
  assert.match(
    statusActionDisabledReason("active", readiness({
      unresolved_blocker_count: 1,
      is_blocked: true,
      is_ready: false
    }), true),
    /incoming blocker/
  );
  assert.match(statusActionDisabledReason("promoted", readiness(), false), /report settings/);
  assert.equal(statusActionDisabledReason("pending", gated, false), null);
});

test("manual closeout records say exactly what the human action proves", () => {
  for (const status of ["done", "wont-do", "promoted"]) {
    const report = humanDecisionReport(work, status, "9");
    assert.match(report.summary, /A person explicitly marked/);
    assert.equal(report.prompt_revision, "9");
    assert.deepEqual(report.fyi_items, []);
  }
  assert.match(humanDecisionCompletionCheckpoint(work), /Explicit human decision/);
  assert.match(humanDecisionCompletionCheckpoint(work), /makes no additional/);
});

test("manual Active responses are exact and bound to the dashboard actor", () => {
  const actor = { actor_client: "dashboard", actor_session_id: "tab" };
  const lease = {
    holder_client: "dashboard",
    holder_session_id: "tab",
    acquired_at: "2026-09-01T12:00:00Z",
    renewed_at: "2026-09-01T12:00:00Z",
    expires_at: "2026-09-01T12:15:00Z"
  };
  assert.deepEqual(decodeDashboardActivationResult(lease, actor), lease);
  assert.throws(
    () => decodeDashboardActivationResult({ ...lease, holder_session_id: "other" }, actor),
    /invalid manual activation/
  );
  assert.throws(
    () => decodeDashboardActivationResult({ ...lease, lease_token: "leaked" }, actor),
    /invalid manual activation/
  );
});

test("manual Pending responses are exact and bound to the selected work item", () => {
  assert.deepEqual(decodeLeaseReleaseResult({
    work_item_id: work.id,
    released: true
  }, work.id), {
    work_item_id: work.id,
    released: true
  });
  assert.throws(() => decodeLeaseReleaseResult({
    work_item_id: "f1cf3691-7d28-4716-94a9-4867b341a685",
    released: true
  }, work.id), /invalid manual Pending/);
  assert.throws(() => decodeLeaseReleaseResult({
    work_item_id: work.id, released: true, token: "leaked"
  }, work.id), /invalid manual Pending/);
});
