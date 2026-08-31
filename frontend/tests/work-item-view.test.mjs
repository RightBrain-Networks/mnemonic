import assert from "node:assert/strict";
import test from "node:test";
import { migrationWarning, normalizedTags, readinessAfterWorkSave } from "../lib/work-item-view.ts";

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

test("identity edits preserve an active session instead of presenting it as ready", () => {
  const active = {
    lifecycle_status: "open",
    is_terminal: false,
    has_active_lease: true,
    active_lease: {
      holder_client: "claude-code",
      holder_session_id: "session-42",
      acquired_at: "2026-08-31T18:00:00Z",
      renewed_at: "2026-08-31T18:00:00Z",
      expires_at: "2026-08-31T18:15:00Z"
    },
    unresolved_blocker_count: 0,
    is_blocked: false,
    is_ready: false,
    display_state: "active"
  };
  assert.deepEqual(readinessAfterWorkSave(active, "open", "open"), active);
});

test("successful lifecycle transitions clear lease visibility and keep blockers authoritative", () => {
  const terminal = {
    lifecycle_status: "done",
    is_terminal: true,
    has_active_lease: false,
    active_lease: null,
    unresolved_blocker_count: 2,
    is_blocked: true,
    is_ready: false,
    display_state: "done"
  };
  assert.deepEqual(readinessAfterWorkSave(terminal, "done", "open"), {
    ...terminal,
    lifecycle_status: "open",
    is_terminal: false,
    is_ready: false,
    display_state: "blocked"
  });
});
