import assert from "node:assert/strict";
import test from "node:test";
import { pendingJobReportIds } from "../lib/pending-job-reports.ts";
import { cursor, envelope, project, reportPage } from "./phase12-fixtures.mjs";

const reportId = (sequence) => `aaaaaaaa-aaaa-4aaa-8aaa-${String(sequence).padStart(12, "0")}`;
const entry = (sequence) => ({
  ...structuredClone(envelope),
  created_sequence: String(sequence),
  report: { ...structuredClone(envelope.report), id: reportId(sequence) }
});
const continuation = (upper, last) => cursor({
  kind: "reports", dismissal: "undismissed", work_item_id: null,
  upper: String(upper), last: String(last)
});
const page = (sequences, more = false, upper = 60) => reportPage({
  as_of_sequence: String(upper), items: sequences.map(entry),
  has_more: more, next_cursor: more ? continuation(upper, sequences.at(-1)) : null
});

test("pending summaries collect every cursor page under the original snapshot using only reads", async (t) => {
  const firstSequences = Array.from({ length: 50 }, (_, index) => 60 - index);
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url: new URL(url, "http://example.test"), init });
    return Response.json(calls.length === 1 ? page(firstSequences, true) : page([10, 9]));
  });

  const ids = await pendingJobReportIds(project, new AbortController().signal);

  assert.deepEqual(ids, [...firstSequences, 10, 9].map(reportId));
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url.searchParams.has("cursor"), false);
  assert.equal(calls[1].url.searchParams.get("cursor"), continuation(60, 11));
  for (const { url, init } of calls) {
    assert.equal(url.pathname, `/api/mnemonic/projects/${project}/job-completion-reports`);
    assert.equal(url.searchParams.get("dismissal"), "undismissed");
    assert.equal(url.searchParams.get("limit"), "50");
    assert.equal(init.method ?? "GET", "GET");
    assert.equal(init.body, undefined);
    assert.ok(init.signal instanceof AbortSignal);
  }
});

test("an empty pending queue requires just one read", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(page([])));
  assert.deepEqual(await pendingJobReportIds(project, new AbortController().signal), []);
  assert.equal(fetch.mock.calls.length, 1);
});

test("malformed continuations reject the whole snapshot instead of returning partial report IDs", async (t) => {
  const otherProject = "99999999-9999-4999-8999-999999999999";
  const malformed = [
    page([59], false, 61),
    { ...page([59]), stream_id: otherProject },
    { ...page([59]), project_id: otherProject },
    page([60]),
    { ...page([59]), has_more: true, next_cursor: continuation(60, 60) },
    { ...page([59]), items: [{ ...entry(59), report: entry(60).report }] },
    { ...page([59]), items: [{ ...entry(59), report: { ...entry(60).report, id: reportId(60).toUpperCase() } }] }
  ];
  let calls = 0;
  let next;
  t.mock.method(globalThis, "fetch", async () => Response.json(++calls === 1 ? page([60], true) : next));
  for (const invalid of malformed) {
    calls = 0; next = invalid;
    await assert.rejects(pendingJobReportIds(project, new AbortController().signal), /invalid|incoherent/i);
    assert.equal(calls, 2, "A malformed chain must stop before another page is fetched");
  }
});

test("a later read failure never returns a partially scanned queue or retries automatically", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ++calls === 1
    ? Response.json(page([60], true))
    : Response.json({ detail: "The report feed is unavailable." }, { status: 503 }));
  await assert.rejects(pendingJobReportIds(project, new AbortController().signal), /report feed is unavailable/);
  assert.equal(calls, 2);
});

test("an already cancelled scan makes no request", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(page([])));
  const controller = new AbortController();
  controller.abort(new DOMException("Closed confirmation", "AbortError"));
  await assert.rejects(pendingJobReportIds(project, controller.signal), { name: "AbortError" });
  assert.equal(fetch.mock.calls.length, 0);
});

test("cancellation rejects a late successful page before returning IDs or fetching the continuation", async (t) => {
  const controller = new AbortController();
  const fetch = t.mock.method(globalThis, "fetch", async () => {
    controller.abort(new DOMException("Changed project", "AbortError"));
    // The transport may complete despite cancellation; the collector must check again.
    return Response.json(page([60], true));
  });
  await assert.rejects(pendingJobReportIds(project, controller.signal), { name: "AbortError" });
  assert.equal(fetch.mock.calls.length, 1);
});

test("scan cancellation reaches the active transport independently of its timeout", async (t) => {
  const controller = new AbortController();
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", (milliseconds) => {
    assert.equal(milliseconds, 30_000);
    return deadline.signal;
  });
  let signal;
  const fetch = t.mock.method(globalThis, "fetch", (_url, init) => new Promise((_resolve, reject) => {
    signal = init.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }));
  const pending = pendingJobReportIds(project, controller.signal);
  const rejected = assert.rejects(pending, { name: "AbortError" });
  controller.abort(new DOMException("Closed confirmation", "AbortError"));
  await rejected;
  assert.equal(signal.aborted, true);
  assert.equal(deadline.signal.aborted, false);
  assert.equal(fetch.mock.calls.length, 1);
});

test("a stalled page times out without cancelling the modal scope or starting an automatic retry", async (t) => {
  const controller = new AbortController();
  const deadline = new AbortController();
  t.mock.method(AbortSignal, "timeout", () => deadline.signal);
  let signal;
  const fetch = t.mock.method(globalThis, "fetch", (_url, init) => new Promise((_resolve, reject) => {
    signal = init.signal;
    signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  }));
  const pending = pendingJobReportIds(project, controller.signal);
  const rejected = assert.rejects(pending, /Cannot reach Mnemonic/);
  deadline.abort(new DOMException("Report scan timed out", "TimeoutError"));
  await rejected;
  assert.equal(signal.aborted, true);
  assert.equal(controller.signal.aborted, false);
  assert.equal(fetch.mock.calls.length, 1);
});
