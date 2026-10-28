import assert from "node:assert/strict";
import test from "node:test";
import { decodeTranscriptFolders, formatTranscriptSize, warningsForTranscriptDirectory } from "../lib/transcript-settings.ts";
import { validTranscriptStorage } from "../lib/transcript-health.ts";

test("transcript sizes scale from bytes through terabytes", () => {
  for (const [bytes, label] of [[0, "0 B"], [511, "511 B"], [1024, "1 KB"], [53248, "52 KB"], [14043.1 * 1024 ** 2, "14 GB"], [1.5 * 1024 ** 4, "1.5 TB"], [null, "Unavailable"]]) assert.equal(formatTranscriptSize(bytes), label);
});

test("folder browsing validates paths and empty, root, partial responses", () => {
  const root = { directory: null, parent: null, entries: [{ name: "Sessions", path: "/approved" }], truncated: false };
  assert.equal(decodeTranscriptFolders(root).entries[0].path, "/approved");
  assert.equal(decodeTranscriptFolders({ ...root, directory: "/approved", entries: [], truncated: true }).truncated, true);
  for (const change of [{ directory: "relative" }, { parent: "/approved/.." }, { truncated: "false" }, { entries: [{ name: "bad", path: "/approved/../private" }] }, { entries: [{ name: "", path: "/approved" }] }]) assert.throws(() => decodeTranscriptFolders({ ...root, ...change }));
});

test("access problems belong to the closest configured folder and include blocked ancestors", () => {
  const directories = ["/sessions", "/sessions/nested", "/sessions-other"];
  const warnings = [{ path: "/sessions/nested/run.jsonl" }, { path: "/sessions/run.jsonl" }, { path: "/sessions-other/run.jsonl" }, { path: null }];
  assert.deepEqual(warningsForTranscriptDirectory("/sessions", directories, warnings), [warnings[1]]);
  assert.deepEqual(warningsForTranscriptDirectory("/sessions/nested", directories, warnings), [warnings[0]]);
  assert.deepEqual(warningsForTranscriptDirectory("/sessions-other", directories, warnings), [warnings[2]]);
  assert.deepEqual(warningsForTranscriptDirectory("/sessions/nested", ["/sessions/nested"], [{ path: "/sessions" }]), [{ path: "/sessions" }]);
  assert.deepEqual(warningsForTranscriptDirectory("/unrelated", ["/unrelated"], warnings), []);
});

test("storage unavailable and partial measurements remain explicit", () => {
  const usage = { checked_at: "2026-09-18T00:00:00Z", bytes: 100, logical_bytes: 1000, free_bytes: 2000, total_bytes: 4000, complete: true, error_code: null };
  const storage = { scope: "all_projects", transcripts: usage, index: null, database_bytes: 8192 };
  assert.equal(validTranscriptStorage(storage), true);
  assert.equal(validTranscriptStorage({ ...storage, transcripts: { ...usage, complete: false, error_code: "transcript_storage_scan_limited" } }), true);
  for (const change of [{ free_bytes: -1 }, { bytes: null }, { total_bytes: "4000" }]) assert.equal(validTranscriptStorage({ ...storage, transcripts: { ...usage, ...change } }), false);
});
