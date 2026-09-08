import assert from "node:assert/strict";
import test from "node:test";
import { backupRoute, boundedBackupStream, proxyBackup } from "../lib/backup-proxy.ts";
import { backupAge, backupMaximumBytes, backupSize, decodeBackup, decodeBackups, definitiveBackupFailure } from "../lib/backups.ts";

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

test("backup routes are project scoped and allow only four dashboard operations", () => {
  assert.equal(backupRoute(root.split("/"), "GET"), "list");
  assert.equal(backupRoute(root.split("/"), "POST"), "create");
  assert.equal(backupRoute(`${root}/${filename}`.split("/"), "GET"), "download");
  assert.equal(backupRoute(restore.split("/"), "POST"), "restore");
  for (const [path, method] of [[root, "DELETE"], [restore, "GET"], ["backups", "POST"], ["projects/invalid/backups", "GET"], [`${root}/../private.bz2`, "GET"], [`${root}/%2e%2e.bz2`, "GET"], [`${root}/uncompressed.sql`, "GET"], [`${root}/x.bz2/extra`, "GET"]]) {
    assert.equal(backupRoute(path.split("/"), method), null);
  }
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

test("backup proxy refuses untrusted requests, mismatched confirmation, encoded bodies and oversize uploads before connecting", async () => {
  let calls = 0;
  const fetcher = async () => { calls++; throw new Error("must not run"); };
  for (const [input, path, status] of [
    [request(root, "POST", { origin: "https://attacker.test" }), root, 403],
    [request(root, "GET", { "sec-fetch-site": "cross-site" }), root, 403],
    [request(root, "POST", { host: "attacker.test" }), root, 403],
    [upload({ "X-Confirm-Project": "wrong" }), restore, 400],
    [upload({ "content-length": "17" }), restore, 413],
    [upload({ "content-length": "-1" }), restore, 400],
    [upload({ "content-type": "text/plain" }), restore, 415],
    [upload({ "content-encoding": "gzip" }), restore, 415],
    [upload({ "X-Lease-Token": "secret" }), restore, 400],
    [request(root, "POST", {}, "unexpected"), root, 400],
    [request(`${root}?token=secret`), root, 400]
  ]) assert.equal((await proxyBackup(input, path.split("/"), environment, fetcher)).status, status);
  assert.equal(calls, 0);
});

test("only recognized service rejection codes establish a definite mutation failure", () => {
  assert.equal(definitiveBackupFailure(409, { error: { code: "backup_project_mismatch", message: "Another project" } }), true);
  assert.equal(definitiveBackupFailure(422, { error: { code: "invalid_backup", message: "Corrupt archive" } }), true);
  assert.equal(definitiveBackupFailure(404, { unrelated: "Intermediary response" }), false);
  assert.equal(definitiveBackupFailure(500, { error: { code: "backup_project_mismatch", message: "Another project" } }), false);
  assert.equal(definitiveBackupFailure(409, { error: { code: "unknown", message: "Unknown outcome" } }), false);
});

test("a bodyless browser POST may arrive from Next as an empty readable stream", async () => {
  const body = new ReadableStream({ start(controller) { controller.close(); } });
  const response = await proxyBackup(request(root, "POST", { "content-length": "0" }, body), root.split("/"), environment, async () => Response.json(archive, { status: 201 }));
  assert.equal(response.status, 201);
});

test("backup proxy uses only the separate token and never forwards browser credentials or service cookies", async () => {
  const noToken = { MNEMONIC_API_KEY: "a".repeat(64) };
  const missing = await proxyBackup(request(), root.split("/"), noToken, async () => { throw new Error("must not run"); });
  assert.equal(missing.status, 503);
  const response = await proxyBackup(request(root, "GET", { authorization: "Bearer client-token", cookie: "session=private" }), root.split("/"), environment, async (target, init) => {
    assert.equal(String(target), `http://backup:8002/${root}`);
    assert.equal(init.headers.get("authorization"), `Bearer ${environment.MNEMONIC_BACKUP_TOKEN}`);
    assert.equal(init.headers.get("cookie"), null);
    assert.equal(init.redirect, "manual");
    return Response.json({ project_id: project, retention_count: 7, backups: [] }, { headers: { "set-cookie": "service=private" } });
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.match(response.headers.get("cache-control"), /no-store/);
});

test("restore bytes stream unchanged and chunked uploads stop at the compressed limit", async () => {
  let received;
  const restored = await proxyBackup(upload(), restore.split("/"), environment, async (_target, init) => {
    received = await new Response(init.body).text();
    assert.equal(init.headers.get("X-Confirm-Project"), project);
    assert.equal(init.headers.get("content-type"), "application/octet-stream");
    return Response.json({ project_id: project, restored: true });
  });
  assert.equal(restored.status, 200);
  assert.equal(received, "BZh");
  let cancelled = false;
  const stream = new ReadableStream({ pull(controller) { controller.enqueue(new Uint8Array(8)); }, cancel() { cancelled = true; } });
  const response = await proxyBackup(upload({}, stream), restore.split("/"), environment, async (_target, init) => {
    await new Response(init.body).arrayBuffer();
    throw new Error("must not reach the restore after an oversized upload");
  });
  assert.equal(response.status, 413);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(cancelled, true);
});

test("downloads stream exact bytes as sandboxed attachments with a path-derived safe filename", async () => {
  const route = `${root}/${filename}`;
  const response = await proxyBackup(request(route), route.split("/"), environment, async () => new Response("BZh", {
    headers: { "Content-Type": "text/html", "Content-Length": "3", "Content-Disposition": "inline; filename=unsafe.html", "Set-Cookie": "secret=value" }
  }));
  assert.equal(await response.text(), "BZh");
  assert.equal(response.headers.get("content-type"), "application/octet-stream");
  assert.equal(response.headers.get("content-disposition"), `attachment; filename="${filename}"`);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("cross-origin-resource-policy"), "same-origin");
  assert.equal(response.headers.get("set-cookie"), null);
  await assert.rejects(new Response(boundedBackupStream(new Response("BZh").body, 4, true)).text(), /declared byte length/);
});

test("redirects, truncated JSON and lost mutation responses fail without any automatic retry", async () => {
  for (const fetcher of [async () => new Response(null, { status: 302, headers: { Location: "https://attacker.test" } }), async () => new Response("{", { headers: { "Content-Type": "application/json" } }), async () => { throw new TypeError("connection reset"); }]) {
    let attempts = 0;
    const response = await proxyBackup(request(root, "POST"), root.split("/"), environment, (...args) => { attempts++; return fetcher(...args); });
    assert.equal(response.status, 502);
    assert.equal(attempts, 1);
  }
});
