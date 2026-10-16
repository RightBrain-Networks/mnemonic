import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { fetchFreshWorkSettings, invalidateWorkSettingsReads, workDetailKeys, workSettingsOptions } from "../lib/work-detail-queries.ts";

const project = "11111111-1111-4111-8111-111111111111";
const firstWork = "22222222-2222-4222-8222-222222222222";
const secondWork = "33333333-3333-4333-8333-333333333333";
const settings = (prompt, revision = "3") => ({
  project_id: project, revision, recall_pointer_template: null,
  job_completion_report_prompt: prompt,
  code_review_required_min_priority: 100, code_review_optional_min_priority: 100,
  allow_remediation_code_reviews: false,
  lease_default_minutes: 15, lease_minimum_minutes: 10, lease_maximum_minutes: 120
});

test("completion preflight cancels an older prompt read and publishes fresh policy to the editor", async (t) => {
  const client = createDashboardQueryClient();
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => requests.push({ url, init, resolve })));
  const options = workSettingsOptions(project, firstWork);
  const editor = new QueryObserver(client, { ...options, refetchOnMount: "always" });
  const unsubscribe = editor.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  const preflight = fetchFreshWorkSettings(client, project, firstWork);
  await setImmediate();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(requests[1].init.signal.aborted, false);
  assert.equal(requests[1].url,
    `/api/mnemonic/projects/${project}/settings?work_item_id=${firstWork}`);
  requests[1].resolve(Response.json(settings("Current review policy", "4")));
  assert.equal((await preflight).revision, "4");
  assert.equal(editor.getCurrentResult().data.job_completion_report_prompt, "Current review policy");
  assert.equal(editor.getCurrentResult().data, client.getQueryData(options.queryKey));
  requests[0].resolve(Response.json(settings("Obsolete review policy")));
  await setImmediate();
  assert.equal(editor.getCurrentResult().data.revision, "4");
});

test("settings notifications refresh active work instructions and mark only scoped policies stale", async (t) => {
  const client = createDashboardQueryClient();
  const otherProject = "44444444-4444-4444-8444-444444444444";
  const first = workSettingsOptions(project, firstWork), inactive = workSettingsOptions(project, secondWork);
  const other = workSettingsOptions(otherProject, firstWork);
  const otherReads = [workDetailKeys.context(project, firstWork), workDetailKeys.checkpoints(project, firstWork, 20, 0)];
  for (const options of [first, inactive, other]) {
    client.setQueryData(options.queryKey, { ...settings("Reviewed instructions"), project_id: options === other ? otherProject : project });
  }
  for (const key of otherReads) client.setQueryData(key, { retained: true });
  const reads = [];
  t.mock.method(globalThis, "fetch", async (url) => {
    reads.push(url);
    return Response.json({ ...settings("Changed instructions", "4"), project_id: url.includes(otherProject) ? otherProject : project });
  });
  const editor = new QueryObserver(client, first), otherEditor = new QueryObserver(client, other);
  const unsubscribe = editor.subscribe(() => {}), unsubscribeOther = otherEditor.subscribe(() => {});
  t.after(() => { unsubscribe(); unsubscribeOther(); client.clear(); });
  const authoredPolicy = editor.getCurrentResult().data;
  await invalidateWorkSettingsReads(client, project);
  assert.equal(reads.length, 1);
  assert.equal(editor.getCurrentResult().data.revision, "4");
  assert.equal(authoredPolicy.revision, "3", "new policy does not silently accept a different authored revision");
  assert.equal(client.getQueryState(inactive.queryKey).isInvalidated, true);
  assert.equal(otherEditor.getCurrentResult().data.revision, "3");
  assert.equal(client.getQueryState(other.queryKey).isInvalidated, false);
  for (const key of otherReads) assert.equal(client.getQueryState(key).isInvalidated, false);
  await invalidateWorkSettingsReads(client, null);
  assert.equal(reads.length, 3);
  assert.equal(otherEditor.getCurrentResult().data.revision, "4");
  for (const key of otherReads) assert.equal(client.getQueryState(key).isInvalidated, false);
});

test("policy invalidation cancels pre-change reads while retaining visible reviewed instructions", async (t) => {
  const client = createDashboardQueryClient();
  const options = workSettingsOptions(project, firstWork);
  const reviewed = settings("Reviewed instructions");
  client.setQueryData(options.queryKey, reviewed);
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => requests.push({ url, init, resolve })));
  const editor = new QueryObserver(client, options);
  const unsubscribe = editor.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  const older = editor.refetch();
  const refresh = invalidateWorkSettingsReads(client, project);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(editor.getCurrentResult().data, reviewed);
  requests[1].resolve(Response.json(settings("New instructions", "4")));
  await Promise.all([older, refresh]);
  requests[0].resolve(Response.json(settings("Obsolete instructions")));
  await setImmediate();
  assert.equal(editor.getCurrentResult().data.revision, "4");
  assert.equal(reviewed.revision, "3");
});

test("changing report work cancels obsolete policy reads without displaying another work's instructions", async (t) => {
  const client = createDashboardQueryClient();
  const requests = [];
  t.mock.method(globalThis, "fetch", (url, init) => new Promise((resolve) => requests.push({ url, init, resolve })));
  const first = workSettingsOptions(project, firstWork), second = workSettingsOptions(project, secondWork);
  const editor = new QueryObserver(client, first);
  const unsubscribe = editor.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  editor.setOptions(second);
  assert.equal(requests.length, 2);
  assert.equal(requests[0].init.signal.aborted, true);
  assert.equal(editor.getCurrentResult().data, undefined);
  requests[0].resolve(Response.json(settings("Obsolete first-work prompt")));
  requests[1].resolve(Response.json(settings("Current second-work prompt")));
  await setImmediate();
  assert.equal(client.getQueryData(first.queryKey), undefined);
  assert.equal(editor.getCurrentResult().data.job_completion_report_prompt, "Current second-work prompt");
});

test("policy refresh retains instructions through malformed responses and exposes the new reviewed revision", async (t) => {
  const client = createDashboardQueryClient();
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(settings("Original instructions")));
  const options = workSettingsOptions(project, firstWork);
  await client.fetchQuery(options);
  const editor = new QueryObserver(client, options);
  const unsubscribe = editor.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  const previous = editor.getCurrentResult().data;
  fetch.mock.mockImplementation(async () => Response.json(settings("Malformed instructions", 4)));
  await editor.refetch();
  assert.equal(editor.getCurrentResult().isRefetchError, true);
  assert.equal(editor.getCurrentResult().data, previous);
  fetch.mock.mockImplementation(async () => Response.json(settings("Updated instructions", "4")));
  await editor.refetch();
  assert.equal(editor.getCurrentResult().data.revision, "4");
  assert.equal(previous.revision, "3", "the authored snapshot remains unchanged by cache refresh");
});
