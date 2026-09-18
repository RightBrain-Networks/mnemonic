import assert from "node:assert/strict";
import test from "node:test";
import { transcriptBytes, transcriptMegabytes } from "../lib/transcript-settings.ts";
import { readDismissedTranscriptWarnings, transcriptWarningKey, validTranscriptStorage } from "../lib/transcript-health.ts";
import { validTranscriptQuery } from "../lib/transcript-proxy.ts";

test("megabyte settings retain exact existing byte values and reject invalid input", () => {
  for (const bytes of [1, 999999, 67108864, 536870912, 1073741824]) {
    assert.equal(transcriptBytes(transcriptMegabytes(bytes)), bytes);
  }
  assert.equal(transcriptBytes("256"), 268435456);
  assert.equal(transcriptMegabytes(67108864), "64");
  for (const invalid of ["", "0", "-1", "1e3", "Infinity", "0.0000001", "1.5.6", " 1", "9007199254740991"]) {
    assert.equal(transcriptBytes(invalid), null);
  }
});

test("dismissal survives retry timestamps but a changed failure remains visible", () => {
  const warning = { code: "transcript_source_missing", path: "/approved/native.jsonl", service: "worker", affected: 1, retry_at: null, uid: 1000, gid: 1000, owner_uid: null, owner_gid: null, mode: null };
  const key = transcriptWarningKey(warning);
  assert.equal(transcriptWarningKey({ ...warning, retry_at: "2026-09-18T00:00:00Z", affected: 2 }), key);
  assert.notEqual(transcriptWarningKey({ ...warning, code: "transcript_permission_denied" }), key);
  assert.deepEqual(readDismissedTranscriptWarnings(JSON.stringify([key])), [key]);
  for (const value of [null, "bad json", "{}", "[1,null]"]) assert.deepEqual(readDismissedTranscriptWarnings(value), []);
});

test("transcript controls validate every server sort field and large text offsets", () => {
  for (const sort of ["name", "size", "session", "indexing", "updated"]) {
    for (const direction of ["asc", "desc"]) assert.equal(validTranscriptQuery(new URLSearchParams({ sort_by: sort, sort_direction: direction }), "list"), true);
  }
  for (const query of ["sort_by=filename", "sort_direction=descending", "sort_by=size&sort_by=name"]) assert.equal(validTranscriptQuery(new URLSearchParams(query), "list"), false);
  assert.equal(validTranscriptQuery(new URLSearchParams({ offset: "9000000", limit: "20000" }), "text"), true);
  assert.equal(validTranscriptQuery(new URLSearchParams({ offset: "1073741825" }), "text"), false);
});

test("storage unavailable and partial measurements remain explicit", () => {
  const usage = { checked_at: "2026-09-18T00:00:00Z", bytes: 100, logical_bytes: 1000, free_bytes: 2000, total_bytes: 4000, complete: true, error_code: null };
  const storage = { scope: "all_projects", transcripts: usage, index: null, database_bytes: 8192 };
  assert.equal(validTranscriptStorage(storage), true);
  assert.equal(validTranscriptStorage({ ...storage, transcripts: { ...usage, complete: false, error_code: "transcript_storage_scan_limited" } }), true);
  for (const change of [{ free_bytes: -1 }, { bytes: null }, { total_bytes: "4000" }]) assert.equal(validTranscriptStorage({ ...storage, transcripts: { ...usage, ...change } }), false);
});
