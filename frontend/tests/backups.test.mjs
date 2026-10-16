import assert from "node:assert/strict";
import test from "node:test";

import { backupAge, backupMaximumBytes, backupSize, decodeBackup, decodeBackupJob, decodeBackups, definitiveBackupFailure } from "../lib/backups.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const root = `projects/${project}/backups`;
const restore = `projects/${project}/restore`;
const filename = "project-20260908T120000123456Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bz2";
const archive = { filename, created_at: "2026-09-08T12:00:00.123456+00:00", size_bytes: 3 };
const environment = { MNEMONIC_BACKUP_TOKEN: "b".repeat(64), MNEMONIC_BACKUP_URL: "http://backup:8002", MNEMONIC_BACKUP_MAX_BYTES: "16" };

function request(route = root, method = "GET", headers = {}, body) {
  return new Request(`http://localhost:3000/api/backups/${route}`, {
    method, headers: { host: "localhost:3000", ...(method === "POST" ? { origin: "http://localhost:3000" } : {}), ...headers },
    body, ...(body instanceof ReadableStream ? { duplex: "half" } : {})
  });
}

const upload = (headers = {}, body = "BZh") => request(restore, "POST", {
  "content-type": "application/octet-stream", "X-Confirm-Project": project, ...headers
}, body);

test("backup jobs validate project, operation identity, terminal results and failures", () => {
  const job = { project_id: project, job_id: project, state: "pending" };
  assert.deepEqual(decodeBackupJob(job, project), job);
  assert.deepEqual(decodeBackupJob({ ...job, state: "succeeded", result: archive }, project).result, archive);
  for (const value of [{ ...job, state: "unknown" }, { ...job, job_id: "bad" },
    { ...job, state: "succeeded" }, { ...job, state: "failed", error: {} }]) {
    assert.throws(() => decodeBackupJob(value, project));
  }
  assert.throws(() => decodeBackupJob(job, "another"));
  assert.throws(() => decodeBackupJob(job, project, "another"));
});

test("backup decoders verify project identity, filenames, ages, counts and compressed byte sizes", () => {
  assert.deepEqual(decodeBackup(archive), archive);
  assert.equal(decodeBackups({ project_id: project, retention_count: 7, backups: [archive] }, project).backups.length, 1);
  for (const value of [null, { ...archive, filename: "../private.bz2" }, { ...archive, created_at: "yesterday" }, { ...archive, size_bytes: 0 }]) assert.throws(() => decodeBackup(value));
  assert.throws(() => decodeBackups({ project_id: "another", retention_count: 7, backups: [archive] }, project));
  assert.throws(() => decodeBackups({ project_id: project, retention_count: 0, backups: [] }, project));
  assert.throws(() => decodeBackups({ project_id: project, retention_count: 7, backups: [archive, archive] }, project));
  assert.equal(backupMaximumBytes(), 64 * 1024 * 1024);
  for (const value of ["", "0", "-1", "NaN", "9007199254740992", "1.5"]) assert.throws(() => backupMaximumBytes(value));
  assert.equal(backupAge(archive.created_at, Date.parse("2026-09-08T13:00:01Z")), "1 hour ago");
  assert.equal(backupAge(archive.created_at, Date.parse("2026-09-10T12:00:01Z")), "2 days ago");
  assert.equal(backupSize(64 * 1024 * 1024), "64.0 MiB");
});

test("only recognized service rejection codes establish a definite mutation failure", () => {
  assert.equal(definitiveBackupFailure(409, { error: { code: "backup_project_mismatch", message: "Another project" } }), true);
  assert.equal(definitiveBackupFailure(422, { error: { code: "invalid_backup", message: "Corrupt archive" } }), true);
  assert.equal(definitiveBackupFailure(404, { unrelated: "Intermediary response" }), false);
  assert.equal(definitiveBackupFailure(500, { error: { code: "backup_project_mismatch", message: "Another project" } }), false);
  assert.equal(definitiveBackupFailure(409, { error: { code: "unknown", message: "Unknown outcome" } }), false);
});
