import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { focusManager, onlineManager, QueryObserver } from "@tanstack/react-query";
import { invalidateCoreReads, projectCatalogOptions, projectSettingsOptions } from "../lib/core-queries.ts";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { queryKeys } from "../lib/query-keys.ts";

const projectId = "11111111-1111-4111-8111-111111111111";
const otherId = "22222222-2222-4222-8222-222222222222";
const project = (id = projectId, name = "Workspace") => ({
  id, name, slug: `project-${id}`, description: "", repository_url: null,
  created_at: "2026-10-15T12:00:00Z", updated_at: "2026-10-15T12:00:00Z"
});
const settings = (id = projectId) => ({
  project_id: id, revision: "3", recall_pointer_template: null,
  job_completion_report_prompt: "Summarize the completed work.",
  code_review_required_min_priority: 100, code_review_optional_min_priority: 100,
  allow_remediation_code_reviews: false,
  lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120
});
const catalog = (items = [], total = items.length, offset = 0) => ({ items, total, limit: 100, offset });

function clientFor(t) {
  const client = createDashboardQueryClient();
  const subscriptions = [];
  t.after(() => {
    for (const unsubscribe of subscriptions) unsubscribe();
    client.clear();
  });
  return { client, observe(options) {
    const observer = new QueryObserver(client, options);
    subscriptions.push(observer.subscribe(() => {}));
    return observer;
  } };
}

test("core invalidation is scoped to the project and requested read domains", async (t) => {
  const { client } = clientFor(t);
  const keys = (id) => ({
    settings: queryKeys.settings(id), attention: queryKeys.attentionCount(id),
    reports: queryKeys.reportCount(id),
    queue: queryKeys.workQueue(id, { search: "migration", status: "pending" }),
    canonical: queryKeys.canonicalWork(id, { search: "migration", excludedWorkId: "source" }),
    tasks: queryKeys.taskPage(id, { status: "active", offset: 0 })
  });
  const current = keys(projectId), other = keys(otherId);
  for (const key of [queryKeys.projects, ...Object.values(current), ...Object.values(other)]) {
    client.setQueryData(key, []);
  }
  await invalidateCoreReads(client, projectId, ["work", "attention"]);
  for (const [name, key] of Object.entries(current)) {
    assert.equal(client.getQueryState(key).isInvalidated, ["queue", "canonical", "tasks", "attention"].includes(name), name);
  }
  for (const key of Object.values(other)) assert.equal(client.getQueryState(key).isInvalidated, false);
  assert.equal(client.getQueryState(queryKeys.projects).isInvalidated, false);
  await invalidateCoreReads(client, projectId, ["settings", "reports"]);
  assert.equal(client.getQueryState(current.settings).isInvalidated, true);
  assert.equal(client.getQueryState(current.reports).isInvalidated, true);
  await invalidateCoreReads(client, "", ["projects", "settings", "work", "attention", "reports"]);
  assert.equal(client.getQueryState(queryKeys.projects).isInvalidated, true);
  for (const key of Object.values(other)) assert.equal(client.getQueryState(key).isInvalidated, false);
});

test("unscoped work notifications invalidate every project's canonical cache and only refetch active readers", async (t) => {
  const { client, observe } = clientFor(t);
  const active = queryKeys.canonicalWork(projectId, { query: "same term", excludedWorkId: "source" });
  const inactive = queryKeys.canonicalWork(otherId, { query: "same term", excludedWorkId: "other" });
  const queue = queryKeys.workQueue(otherId, { status: "pending" });
  const tasks = queryKeys.taskPage(otherId, { status: "active" });
  const unrelated = queryKeys.settings(otherId);
  for (const key of [active, inactive, queue, tasks, unrelated]) client.setQueryData(key, []);
  let activeReads = 0, inactiveReads = 0;
  observe({ queryKey: active, queryFn: async () => { activeReads += 1; return ["fresh"]; } });
  // Keep this query cached with its real fetch function but without an observer.
  client.getQueryCache().find({ queryKey: inactive }).setOptions({
    queryKey: inactive, queryFn: async () => { inactiveReads += 1; return ["unexpected"]; }
  });
  await invalidateCoreReads(client, null, ["work"]);
  assert.equal(activeReads, 1);
  assert.deepEqual(client.getQueryData(active), ["fresh"]);
  assert.equal(client.getQueryState(active).isInvalidated, false);
  assert.equal(inactiveReads, 0);
  for (const key of [inactive, queue, tasks]) assert.equal(client.getQueryState(key).isInvalidated, true);
  assert.equal(client.getQueryState(unrelated).isInvalidated, false);
});

test("catalog cancellation aborts the shared signal during pagination and never caches a partial or late result", async (t) => {
  const { client } = clientFor(t);
  const firstPage = Array.from({ length: 100 }, (_, index) => project(
    `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
    `Project ${index}`
  ));
  const late = Promise.withResolvers();
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => {
    requests.push({ url, init });
    return requests.length === 1 ? Promise.resolve(Response.json(catalog(firstPage, 101))) : late.promise;
  });
  const options = projectCatalogOptions();
  const first = client.fetchQuery(options);
  const second = client.fetchQuery(options);
  const settled = Promise.allSettled([first, second]);
  await setImmediate();
  assert.equal(requests.length, 2);
  assert.equal(requests[1].url, "/api/mnemonic/projects?limit=100&offset=100");
  assert.equal(requests[0].init.signal, requests[1].init.signal);
  assert.equal(client.getQueryData(options.queryKey), undefined);
  await client.cancelQueries({ queryKey: options.queryKey });
  assert.equal(requests[1].init.signal.aborted, true);
  assert.deepEqual((await settled).map((result) => result.status), ["rejected", "rejected"]);
  late.resolve(Response.json(catalog([project(otherId)], 101, 100)));
  await setImmediate();
  assert.equal(client.getQueryData(options.queryKey), undefined);
});

test("settings observers cancel the previous project before accepting the next project's decoded data", async (t) => {
  const { client, observe } = clientFor(t);
  const reads = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => reads.push({ url, init, resolve })));
  const observer = observe(projectSettingsOptions(projectId));
  observer.setOptions(projectSettingsOptions(otherId));
  assert.equal(reads.length, 2);
  assert.equal(reads[0].init.signal.aborted, true);
  assert.equal(observer.getCurrentResult().data, undefined);
  reads[0].resolve(Response.json(settings()));
  reads[1].resolve(Response.json(settings(otherId)));
  await setImmediate();
  assert.deepEqual(observer.getCurrentResult().data, settings(otherId));
  assert.equal(client.getQueryData(queryKeys.settings(projectId)), undefined);
});

test("a loaded empty catalog stays available through background refresh and a failed decode", async (t) => {
  const { client, observe } = clientFor(t);
  const options = projectCatalogOptions();
  const late = Promise.withResolvers();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(catalog()));
  await client.fetchQuery(options);
  const observer = observe(options);
  const loaded = observer.getCurrentResult().data;
  fetch.mock.mockImplementation(() => late.promise);
  const refresh = invalidateCoreReads(client, projectId, ["projects"]);
  assert.equal(observer.getCurrentResult().data, loaded);
  assert.equal(observer.getCurrentResult().isPending, false);
  assert.equal(observer.getCurrentResult().isRefetching, true);
  late.resolve(Response.json(catalog([], 1)));
  await refresh;
  assert.equal(observer.getCurrentResult().data, loaded);
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  assert.equal(fetch.mock.callCount(), 2);
  fetch.mock.mockImplementation(async () => Response.json(catalog([project()])));
  await observer.refetch();
  assert.deepEqual(observer.getCurrentResult().data, [project()]);
  assert.equal(observer.getCurrentResult().isError, false);
});

test("pending restores retain the cached catalog while reporting read failures and accepting recovery", async (t) => {
  const { client, observe } = clientFor(t);
  let pending = false;
  const options = projectCatalogOptions(() => pending);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(catalog([project()])));
  await client.fetchQuery(options);
  const observer = observe(options);
  const loaded = observer.getCurrentResult().data;

  // The restore can start while a catalog request is already in flight.
  const late = Promise.withResolvers();
  fetch.mock.mockImplementation(() => late.promise);
  const refresh = invalidateCoreReads(client, projectId, ["projects"]);
  pending = true;
  late.resolve(Response.json(catalog()));
  await refresh;
  assert.equal(observer.getCurrentResult().data, loaded);

  fetch.mock.mockImplementation(async () => Response.json({ detail: "Catalog unavailable" }, { status: 503 }));
  await observer.refetch();
  assert.equal(observer.getCurrentResult().data, loaded);
  assert.equal(observer.getCurrentResult().isRefetchError, true);
  fetch.mock.mockImplementation(async () => Response.json(catalog()));
  await observer.refetch();
  assert.equal(observer.getCurrentResult().data, loaded);
  assert.equal(observer.getCurrentResult().isError, false);

  pending = false;
  await invalidateCoreReads(client, projectId, ["projects"]);
  assert.deepEqual(observer.getCurrentResult().data, []);
});

test("dashboard queries leave failure retries and focus or reconnect refresh to the existing coordinator", async (t) => {
  const { client, observe } = clientFor(t);
  const focused = focusManager.isFocused(), online = onlineManager.isOnline();
  client.mount();
  t.after(() => {
    client.unmount();
    focusManager.setFocused(focused);
    onlineManager.setOnline(online);
  });
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(settings(otherId)));
  const observer = observe({ ...projectSettingsOptions(projectId), staleTime: 0 });
  await setImmediate();
  assert.equal(observer.getCurrentResult().isError, true);
  assert.match(observer.getCurrentResult().error.message, /invalid project settings/);
  assert.equal(client.getQueryData(queryKeys.settings(projectId)), undefined);
  assert.equal(fetch.mock.callCount(), 1);
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  onlineManager.setOnline(false);
  onlineManager.setOnline(true);
  await setImmediate();
  assert.equal(fetch.mock.callCount(), 1);
  fetch.mock.mockImplementation(async () => Response.json(settings()));
  await observer.refetch();
  assert.deepEqual(observer.getCurrentResult().data, settings());
  assert.equal(fetch.mock.callCount(), 2);

  let attempts = 0;
  const mutation = client.getMutationCache().build(client, {
    mutationFn: async () => { attempts += 1; throw new Error("Uncertain write"); }
  });
  await assert.rejects(mutation.execute(), /Uncertain write/);
  assert.equal(attempts, 1);
});


test("secondary lists and policy reads share project-scoped live invalidation with their badges", async (t) => {
  const { client } = clientFor(t);
  const groups = {
    settings: ["settings", "prompts", "priority-rubric", "backups"],
    attention: ["attention", "attention-count"],
    reports: ["reports", "report-count"]
  };
  const all = Object.values(groups).flat();
  for (const id of [projectId, otherId]) for (const root of all) {
    client.setQueryData([...queryKeys.project(id), root, "page"], []);
  }
  for (const [domain, roots] of Object.entries(groups)) {
    await invalidateCoreReads(client, projectId, [domain]);
    for (const root of roots) {
      assert.equal(client.getQueryState([...queryKeys.project(projectId), root, "page"]).isInvalidated, true);
      assert.equal(client.getQueryState([...queryKeys.project(otherId), root, "page"]).isInvalidated, false);
    }
  }
  await invalidateCoreReads(client, null, ["settings", "attention", "reports"]);
  for (const root of all) assert.equal(client.getQueryState([...queryKeys.project(otherId), root, "page"]).isInvalidated, true);
});
