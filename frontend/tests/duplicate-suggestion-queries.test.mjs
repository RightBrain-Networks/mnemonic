import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { duplicateSuggestionOptions } from "../lib/duplicate-suggestion-queries.ts";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { semanticDisposition } from "./search-ranking-fixtures.mjs";

const project = "11111111-1111-4111-8111-111111111111";
const request = { title: "Draft", summary: "Summary", initial_prompt: "Context", tags: [], exclude_work_item_id: null, limit: 5 };
const page = () => ({ semantic: semanticDisposition("unavailable"), items: [], limit: 5,
  mode: "lexical", semantic_available: false, semantic_scope: "unavailable",
  composition_version: "duplicate-suggestion-v1", exact_title_group_total: 0, omitted_exact_title_group_count: 0 });

test("draft comparison stays explicit through mount and invalidation, and each action reads fresh data", async (t) => {
  const client = createDashboardQueryClient();
  const options = duplicateSuggestionOptions(project, request);
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    calls.push({ url, init });
    return Response.json(page());
  });
  const observer = new QueryObserver(client, options);
  const unsubscribe = observer.subscribe(() => {});
  t.after(() => { unsubscribe(); client.clear(); });
  assert.equal(calls.length, 0);
  await client.fetchQuery(options);
  await client.invalidateQueries({ queryKey: ["mnemonic"] });
  assert.equal(calls.length, 1);
  assert.deepEqual(observer.getCurrentResult().data, page());
  await client.fetchQuery(options);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].init.body, JSON.stringify(request));
  assert.equal(calls[0].init.method, "POST");
  assert.equal(options.gcTime, 0);
});

test("draft, external evidence and project changes have distinct identities and cancelled responses cannot enter cache", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const options = duplicateSuggestionOptions(project, request);
  for (const changed of [
    duplicateSuggestionOptions("22222222-2222-4222-8222-222222222222", request),
    duplicateSuggestionOptions(project, { ...request, initial_prompt: "New context" }),
    duplicateSuggestionOptions(project, { ...request, external_candidates: [{ title: "External", body: "Evidence", state: "open", url: "https://example.com/record" }] })
  ]) assert.notDeepEqual(changed.queryKey, options.queryKey);
  const late = Promise.withResolvers();
  let signal;
  t.mock.method(globalThis, "fetch", (_url, init) => { signal = init.signal; return late.promise; });
  const read = client.fetchQuery(options);
  const settled = Promise.allSettled([read]);
  await client.cancelQueries({ queryKey: options.queryKey, exact: true });
  assert.equal(signal.aborted, true);
  assert.equal((await settled)[0].status, "rejected");
  late.resolve(Response.json(page()));
  await setImmediate();
  assert.equal(client.getQueryData(options.queryKey), undefined);
});

test("failed or malformed comparisons are never retried automatically", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json({ invalid: true }));
  await assert.rejects(client.fetchQuery(duplicateSuggestionOptions(project, request)), /invalid|incoherent/);
  assert.equal(fetch.mock.callCount(), 1);
});
