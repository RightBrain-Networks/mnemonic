import assert from "node:assert/strict";
import test from "node:test";

import { backupAge, backupMaximumBytes, backupSize, decodeBackup, decodeBackupJob, decodeBackups, decodeBackupSchedule, definitiveBackupFailure } from "../lib/backups.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const root = `projects/${project}/backups`;
const restore = `projects/${project}/restore`;
const filename = "project-20260908T120000123456Z-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json.bz2";
const archive = { filename, created_at: "2026-09-08T12:00:00.123456+00:00", size_bytes: 3 };
const catalog = { project_id: project, schedule: { enabled: false, time: "02:00", timezone: "UTC", retention_days: 7 }, storage: { path: "/backups", total_bytes: 1000, free_bytes: 900, error: null }, latest_job: null };
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
  assert.equal(decodeBackups({ ...catalog, backups: [archive] }, project).backups.length, 1);
  for (const value of [null, { ...archive, filename: "../private.bz2" }, { ...archive, created_at: "yesterday" }, { ...archive, size_bytes: 0 }]) assert.throws(() => decodeBackup(value));
  assert.throws(() => decodeBackups({ ...catalog, project_id: "another", backups: [archive] }, project));
  assert.equal(decodeBackups({ ...catalog, schedule: { ...catalog.schedule, retention_days: 0 }, backups: [] }, project).schedule.retention_days, 0);
  assert.throws(() => decodeBackups({ ...catalog, backups: [archive, archive] }, project));
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


test("backup catalog verifies scheduling, storage coverage and job ownership", () => {
  const value = { ...catalog, backups: [] };
  for (const schedule of [{ ...catalog.schedule, time: "24:00" }, { ...catalog.schedule, timezone: "nowhere" }, { ...catalog.schedule, retention_days: -1 }, { ...catalog.schedule, retention_days: 1.5 }]) {
    assert.throws(() => decodeBackupSchedule(schedule));
  }
  assert.throws(() => decodeBackups({ ...value, latest_job: { project_id: "other", job_id: project, state: "pending" } }, project));
  assert.throws(() => decodeBackups({ ...value, storage: { ...catalog.storage, free_bytes: 1001 } }, project));
  assert.deepEqual(decodeBackups({ ...value, storage: { path: "/backups", total_bytes: null, free_bytes: null, error: "Volume unavailable" } }, project).storage,
    { path: "/backups", total_bytes: null, free_bytes: null, error: "Volume unavailable" });
});
