import assert from "node:assert/strict";
import test from "node:test";
import {
  editableLifecycleStatuses,
  migrationWarning,
  normalizedTags,
  readinessAfterWorkSave,
  terminalActionDisabled,
  terminalActionGateExplanation
} from "../lib/work-item-view.ts";

test("legacy snapshots receive an explicit provenance limitation warning", () => {
  const warning = migrationWarning("legacy-handoff-snapshot");
  assert.match(warning, /preserved exactly/);
  assert.match(warning, /did not record who made later prompt edits/);
  assert.equal(migrationWarning(null), null);
});

test("dashboard checkpoint tags are normalized, deduplicated, bounded, and ordered", () => {
  assert.deepEqual(normalizedTags(" API,phase-1, api,  Ready  "), ["api", "phase-1", "ready"]);
  assert.equal(normalizedTags(Array.from({ length: 25 }, (_, index) => `tag-${index}`).join(",")).length, 20);
});

test("identity edits preserve an active session instead of presenting it as pending", () => {
  const active = {
    lifecycle_status: "pending",
    is_terminal: false,
    has_active_lease: true,
    has_dropped_lease: false,
    active_lease: {
      holder_client: "claude-code",
      holder_session_id: "session-42",
      acquired_at: "2026-08-31T18:00:00Z",
      renewed_at: "2026-08-31T18:00:00Z",
      expires_at: "2026-08-31T18:15:00Z"
    },
    unresolved_blocker_count: 0,
    is_blocked: false,
    unresolved_gate_count: 0,
    is_gated: false,
    is_ready: false,
    display_state: "active"
  };
  assert.deepEqual(readinessAfterWorkSave(active, "pending", "pending"), active);
});

test("successful lifecycle transitions clear lease visibility and keep blockers authoritative", () => {
  const terminal = {
    lifecycle_status: "done",
    is_terminal: true,
    has_active_lease: false,
    has_dropped_lease: false,
    active_lease: null,
    unresolved_blocker_count: 2,
    is_blocked: true,
    unresolved_gate_count: 0,
    is_gated: false,
    is_ready: false,
    display_state: "done"
  };
  assert.deepEqual(readinessAfterWorkSave(terminal, "done", "pending"), {
    ...terminal,
    lifecycle_status: "pending",
    is_terminal: false,
    is_ready: false,
    display_state: "blocked"
  });
});

test("returning gated work to pending presents waiting and never ready", () => {
  const gated = {
    lifecycle_status: "deferred",
    is_terminal: false,
    has_active_lease: false,
    has_dropped_lease: false,
    active_lease: null,
    unresolved_blocker_count: 0,
    is_blocked: false,
    unresolved_gate_count: 2,
    is_gated: true,
    is_ready: false,
    display_state: "deferred"
  };
  assert.deepEqual(readinessAfterWorkSave(gated, "deferred", "pending"), {
    ...gated,
    lifecycle_status: "pending",
    is_ready: false,
    display_state: "waiting"
  });
});

test("lifecycle choices are restricted by the persisted status", () => {
  assert.deepEqual(editableLifecycleStatuses("pending"), ["pending", "wont-do", "promoted"]);
  assert.deepEqual(editableLifecycleStatuses("deferred"), ["deferred", "pending"]);
  assert.deepEqual(editableLifecycleStatuses("done"), ["done", "pending"]);
  assert.deepEqual(editableLifecycleStatuses("wont-do"), ["wont-do", "pending"]);
  assert.deepEqual(editableLifecycleStatuses("promoted"), ["promoted", "pending"]);
});

test("terminal actions share the gate guard and a count-specific visible explanation", () => {
  const readiness = {
    lifecycle_status: "pending",
    is_terminal: false,
    has_active_lease: false,
    has_dropped_lease: false,
    active_lease: null,
    unresolved_blocker_count: 0,
    is_blocked: false,
    unresolved_gate_count: 0,
    is_gated: false,
    is_ready: true,
    display_state: "pending"
  };
  assert.equal(terminalActionDisabled(readiness), false);
  assert.equal(terminalActionDisabled(readiness, true), true);
  assert.equal(terminalActionGateExplanation(readiness, "deletion"), null);

  const oneGate = {
    ...readiness,
    unresolved_gate_count: 1,
    is_gated: true,
    is_ready: false,
    display_state: "waiting"
  };
  assert.equal(terminalActionDisabled(oneGate), true);
  assert.equal(
    terminalActionGateExplanation(oneGate, "completion"),
    "1 unresolved human question blocks completion."
  );
  assert.equal(
    terminalActionGateExplanation({ ...oneGate, unresolved_gate_count: 2 }, "deletion"),
    "2 unresolved human questions block deletion."
  );
});
