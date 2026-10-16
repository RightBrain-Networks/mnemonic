import assert from "node:assert/strict";
import test from "node:test";
import { decodeTranscriptHealth } from "../lib/transcript-health.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const warning = { code: "transcript_permission_denied", service: "worker", path: "/private/<script>.jsonl",
  message: "Permission denied", action: "UID 1026 needs read (r) permission", affected: 1,
  retry_at: "2026-09-17T01:00:00Z", uid: 1026, gid: 1000, owner_uid: 1026, owner_gid: 1000, mode: "0000" };
const health = { project_id: project, checked_at: "2026-09-17T01:00:00Z", worker_checked_at: null,
  warnings: [warning], warnings_omitted: 0, affected_transcripts: 1, recheck_seconds: 300 };

test("health decoder binds project and validates bounded typed permission diagnostics", () => {
  assert.equal(decodeTranscriptHealth(health, project).warnings[0].path, warning.path);
  for (const changed of [{ project_id: crypto.randomUUID() }, { warnings: Array(51).fill(warning) },
    { warnings: [{ ...warning, uid: -1 }] }, { warnings: [{ ...warning, mode: "77777" }] },
    { warnings: [{ ...warning, retry_at: "tomorrow" }] }, { warnings_omitted: -1 }]) {
    assert.throws(() => decodeTranscriptHealth({ ...health, ...changed }, project));
  }
});
