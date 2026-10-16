import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { taskPageQueryOptions } from "../lib/task-queries.ts";

const project = "11111111-1111-4111-8111-111111111111";
const otherProject = "44444444-4444-4444-8444-444444444444";
const work = "22222222-2222-4222-8222-222222222222";
const review = "33333333-3333-4333-8333-333333333333";
const otherReview = "55555555-5555-4555-8555-555555555555";
const task = {
  id: review, kind: "code_review", project_id: project, work_item_id: work, work_version: 3,
  title: "Review cache changes", summary: "Check concurrent readers.", status: "pending", review_state: "requested",
  updated_at: "2026-09-19T12:00:00Z", lease: null
};
const page = (projectId = project, items = []) => ({
  project_id: projectId, work_items: { active: 0, pending: 5 }, code_reviews: { active: 0, pending: 1 },
  next_lease_expires_at: null, items, total: items.length, limit: 20, offset: 0
});

function clientFor(t) {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  return client;
}

test("task reads share a validated cache and forward request filters and the query signal", async (t) => {
  const client = clientFor(t);
  const requests = [];
  let respond;
  t.mock.method(globalThis, "fetch", (url, init) => {
    requests.push({ url, init });
    return new Promise((resolve) => { respond = resolve; });
  });
  const options = taskPageQueryOptions(project, { status: "pending", kind: "code_review", taskId: review });
  const first = client.fetchQuery(options);
  const second = client.fetchQuery(options);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `/api/mnemonic/projects/${project}/tasks?status=pending&limit=20&offset=0&kind=code_review&task_id=${review}`);
  assert.ok(requests[0].init.signal instanceof AbortSignal);
  respond(Response.json(page(project, [task])));
  assert.deepEqual(await first, page(project, [task]));
  assert.deepEqual(await second, page(project, [task]));
  assert.deepEqual(await client.fetchQuery(options), page(project, [task]));
  assert.equal(requests.length, 1);
});

test("disabled task bootstrap waits, and changing project cancels the old read and ignores its late response", async (t) => {
  const client = createDashboardQueryClient();
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((respond) => requests.push({ url, init, respond })));
  const first = taskPageQueryOptions(project, { status: "pending" });
  const second = taskPageQueryOptions(otherProject, { status: "pending" });
  const observer = new QueryObserver(client, { ...first, enabled: false });
  const unsubscribe = observer.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  assert.equal(requests.length, 0);
  observer.setOptions({ ...first, enabled: true });
  assert.equal(requests.length, 1);
  observer.setOptions({ ...second, enabled: true });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  requests[0].respond(Response.json(page()));
  requests[1].respond(Response.json(page(otherProject)));
  await setImmediate();
  assert.deepEqual(observer.getCurrentResult().data, page(otherProject));
  assert.equal(client.getQueryData(first.queryKey), undefined);
});

test("task filters and exact episode selections never reuse another page's rows", async (t) => {
  const client = createDashboardQueryClient();
  const options = taskPageQueryOptions(project, { status: "pending", kind: "code_review" });
  client.setQueryData(options.queryKey, page(project, [task]));
  const observer = new QueryObserver(client, { ...options, enabled: false });
  const unsubscribe = observer.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  assert.equal(observer.getCurrentResult().data.items[0].id, review);
  for (const changed of [
    { status: "done", kind: "code_review" },
    { status: "pending", kind: "work_item" },
    { status: "pending", kind: "code_review", offset: 20 },
    { status: "pending", kind: "code_review", taskId: review },
    { status: "pending", kind: "code_review", taskId: otherReview }
  ]) {
    observer.setOptions({ ...taskPageQueryOptions(project, changed), enabled: false });
    assert.equal(observer.getCurrentResult().data, undefined);
  }
  assert.deepEqual(client.getQueryData(options.queryKey), page(project, [task]));
});

test("task query rejects a mismatched project or exact episode before cache publication", async (t) => {
  const client = clientFor(t);
  let payload = page(otherProject);
  t.mock.method(globalThis, "fetch", async () => Response.json(payload));
  const listOptions = taskPageQueryOptions(project, { status: "pending" });
  await assert.rejects(client.fetchQuery(listOptions), /invalid task page/);
  assert.equal(client.getQueryData(listOptions.queryKey), undefined);
  payload = page(project, [task]);
  const exactOptions = taskPageQueryOptions(project, { status: "all", kind: "code_review", taskId: otherReview });
  await assert.rejects(client.fetchQuery(exactOptions), /different task than requested/);
  assert.equal(client.getQueryData(exactOptions.queryKey), undefined);
});

test("failed task refresh retains the validated page and permits explicit recovery without query retries", async (t) => {
  const client = clientFor(t);
  const options = taskPageQueryOptions(project, { status: "pending" });
  let payload = page();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(payload));
  await client.fetchQuery(options);
  payload = { ...page(), project_id: otherProject };
  await assert.rejects(client.fetchQuery({ ...options, staleTime: 0 }), /invalid task page/);
  assert.equal(fetch.mock.callCount(), 2);
  assert.deepEqual(client.getQueryData(options.queryKey), page());
  assert.equal(client.getQueryState(options.queryKey).status, "error");
  payload = { ...page(), work_items: { active: 0, pending: 8 } };
  assert.deepEqual(await client.fetchQuery({ ...options, staleTime: 0 }), payload);
  assert.equal(client.getQueryState(options.queryKey).status, "success");
  assert.equal(fetch.mock.callCount(), 3);
});
