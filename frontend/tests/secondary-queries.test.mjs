import assert from "node:assert/strict";
import test from "node:test";
import { InfiniteQueryObserver, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { backupCatalogOptions, backupJobOptions, humanAttentionOptions, jobReportListOptions, priorityRubricOptions, promptDetailOptions, secondaryKeys } from "../lib/secondary-queries.ts";
import { cursor, envelope, project, reportId, reportPage, timestamp, work } from "./phase12-fixtures.mjs";

const otherProject = "99999999-9999-4999-8999-999999999999";
const prompt = (content = "Read the project context.") => ({ id: "recall-pointer", name: "Recall pointer", description: "Read context", size_bytes: Buffer.byteLength(content), created_at: timestamp, updated_at: timestamp, revision: "a".repeat(64), content });
const jobId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

// Query owns server snapshots; component editor baselines are deliberately separate.
test("prompt refresh retains the accepted snapshot on read errors and project switches cancel late reads", async (t) => {
  const client = createDashboardQueryClient();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(prompt()));
  const observer = new QueryObserver(client, promptDetailOptions(project, "recall-pointer"));
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await observer.refetch();
  const retained = observer.getCurrentResult().data;
  fetch.mock.mockImplementation(async () => Response.json({ ...prompt(), id: "resume-work" }));
  await observer.refetch();
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  assert.equal(observer.getCurrentResult().data, retained);
  const held = Promise.withResolvers();
  let signal;
  fetch.mock.mockImplementation((url, init) => {
    if (url.includes(otherProject)) return Promise.resolve(Response.json(prompt("Other project instructions.")));
    signal = init.signal; return held.promise;
  });
  const pending = observer.refetch();
  observer.setOptions(promptDetailOptions(otherProject, "recall-pointer"));
  assert.equal(signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  await observer.refetch();
  held.resolve(Response.json(prompt("Late original project instructions.")));
  await pending;
  assert.equal(observer.getCurrentResult().data.content, "Other project instructions.");
  assert.equal(client.getQueryData(secondaryKeys.prompt(project, "recall-pointer")), retained);
});

test("attention page identities include project, exact cursor and optional work filter", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const urls = [];
  t.mock.method(globalThis, "fetch", async (url) => { urls.push(url); return Response.json({ items: [], total: 0, limit: 30, next_cursor: null }); });
  const options = [humanAttentionOptions(project, undefined, null), humanAttentionOptions(project, work, null),
    humanAttentionOptions(project, work, "opaque+cursor="), humanAttentionOptions(otherProject, work, null)];
  for (const option of options) await client.fetchQuery(option);
  assert.equal(client.getQueryCache().getAll().length, 4);
  assert.equal(new URL(urls[2], "http://example.test").searchParams.get("cursor"), "opaque+cursor=");
  assert.equal(new URL(urls[2], "http://example.test").searchParams.get("work_item_id"), work);
  assert.equal(new URL(urls[2], "http://example.test").searchParams.get("limit"), "30");
});

test("backup status polling stops for terminal states and read errors, and validates job authority", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const options = backupJobOptions(project, jobId);
  assert.equal(options.refetchIntervalInBackground, false);
  for (const state of ["pending", "running"]) assert.equal(options.refetchInterval({ state: { data: { state }, error: null } }), 1000);
  for (const state of ["succeeded", "failed"]) assert.equal(options.refetchInterval({ state: { data: { state }, error: null } }), false);
  assert.equal(options.refetchInterval({ state: { data: { state: "pending" }, error: Error("offline") } }), false);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({ project_id: otherProject, job_id: jobId, state: "pending" }));
  await assert.rejects(client.fetchQuery(options), /invalid job/);
  assert.equal(fetch.mock.calls.length, 1);
  fetch.mock.mockImplementation(async () => Response.json({ project_id: project, job_id: jobId, state: "running" }));
  assert.equal((await client.fetchQuery(options)).state, "running");
  assert.match(fetch.mock.calls.at(-1).arguments[0], new RegExp(`/api/backups/projects/${project}/backup-jobs/${jobId}$`));
  assert.ok(fetch.mock.calls.at(-1).arguments[1].signal instanceof AbortSignal);
});

test("backup catalog and priority rubric reject foreign project responses without erasing cached data", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const fetch = t.mock.method(globalThis, "fetch", async (url) => Response.json(url.includes("priority-rubric")
    ? { project_id: project, revision: "1", content: "Prioritize impact." }
    : { project_id: project, retention_count: 2, backups: [] }));
  for (const options of [backupCatalogOptions(project), priorityRubricOptions(project)]) {
    const accepted = await client.fetchQuery(options);
    fetch.mock.mockImplementation(async () => Response.json({ ...accepted, project_id: otherProject }));
    await assert.rejects(client.fetchQuery({ ...options, staleTime: 0 }));
    assert.equal(client.getQueryData(options.queryKey), accepted);
    fetch.mock.mockImplementation(async () => Response.json({ project_id: project, revision: "1", content: "Prioritize impact." }));
  }
});

const reportEntry = (sequence) => ({ ...structuredClone(envelope), created_sequence: String(sequence), report: {
  ...structuredClone(envelope.report), id: sequence === 9 ? reportId : `aaaaaaaa-aaaa-4aaa-8aaa-${String(sequence).padStart(12, "0")}`
} });
const reportCursor = (upper, last) => cursor({ kind: "reports", dismissal: "undismissed", work_item_id: null, upper: String(upper), last: String(last) });
const reports = (sequences, upper = 9, more = false) => reportPage({ as_of_sequence: String(upper), items: sequences.map(reportEntry), has_more: more, next_cursor: more ? reportCursor(upper, sequences.at(-1)) : null });

test("report continuations reject mismatched snapshots and duplicate identities; refresh rebuilds the cursor chain", async (t) => {
  const client = createDashboardQueryClient();
  let next = reports([8]);
  let refreshed = false;
  const cursors = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    const cursor = new URL(url, "http://example.test").searchParams.get("cursor");
    cursors.push(cursor);
    return Response.json(refreshed ? cursor === null ? reports([10, 9], 10, true) : reports([8, 7], 10)
      : cursor === null ? reports([9], 9, true) : next);
  });
  const observer = new InfiniteQueryObserver(client, jobReportListOptions(project));
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await observer.refetch();
  const retained = observer.getCurrentResult().data;
  for (const invalid of [reports([8], 10), reportPage({ items: [{ ...reportEntry(8), report: reportEntry(9).report }] })]) {
    next = invalid;
    await observer.fetchNextPage();
    assert.equal(observer.getCurrentResult().isFetchNextPageError, true);
    assert.equal(observer.getCurrentResult().data, retained);
  }
  next = reports([8]);
  await observer.fetchNextPage();
  refreshed = true;
  await client.invalidateQueries({ queryKey: secondaryKeys.reports(project) });
  assert.deepEqual(cursors.slice(-2), [null, reportCursor(10, 9)]);
  assert.deepEqual(observer.getCurrentResult().data.pages.flatMap((page) => page.items.map((entry) => entry.created_sequence)), ["10", "9", "8", "7"]);
  assert.equal(observer.getCurrentResult().hasNextPage, false);
});

test("backup progress retains the 30-second transport deadline alongside Query cancellation", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const deadlines = [];
  t.mock.method(AbortSignal, "timeout", (milliseconds) => {
    assert.equal(milliseconds, 30_000);
    const deadline = new AbortController();
    deadlines.push(deadline);
    return deadline.signal;
  });
  const signals = [];
  t.mock.method(globalThis, "fetch", (_url, init) => new Promise((_resolve, reject) => {
    signals.push(init.signal);
    init.signal.addEventListener("abort", () => reject(init.signal.reason), { once: true });
  }));
  const options = backupJobOptions(project, jobId);
  const first = client.fetchQuery(options);
  const firstRejected = assert.rejects(first);
  await client.cancelQueries({ queryKey: options.queryKey, exact: true });
  await firstRejected;
  assert.equal(signals[0].aborted, true);
  assert.equal(deadlines[0].signal.aborted, false, "Query cancels the transport independently of its timeout");
  const second = client.fetchQuery(options);
  const timedOut = assert.rejects(second, /Backup status timed out/);
  deadlines[1].abort(new DOMException("Backup status timed out", "TimeoutError"));
  await timedOut;
  assert.equal(signals[1].aborted, true);
  assert.equal(signals.length, 2, "A timeout must not start an automatic status retry");
});
