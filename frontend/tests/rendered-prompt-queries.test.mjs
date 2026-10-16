import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { CancelledError, QueryObserver } from "@tanstack/react-query";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { fetchFreshRenderedPrompt, renderedPromptOptions } from "../lib/secondary-queries.ts";

const project = "11111111-1111-4111-8111-111111111111";
const work = "22222222-2222-4222-8222-222222222222";
const review = "33333333-3333-4333-8333-333333333333";
const rendered = (content = "Current rendered instructions.", changes = {}) => ({
  project_id: project, prompt_id: "recall-pointer", work_item_id: work,
  code_review_id: null, content, revision: "a".repeat(64), prompt_revision: null, ...changes
});

test("render identities bind every rendering input and stay explicit through mount and invalidation", async (t) => {
  const client = createDashboardQueryClient();
  const options = renderedPromptOptions(project, "recall-pointer", work, review);
  for (const changed of [renderedPromptOptions(work, "recall-pointer", work, review),
    renderedPromptOptions(project, "warm-code-review", work, review),
    renderedPromptOptions(project, "recall-pointer", project, review),
    renderedPromptOptions(project, "recall-pointer", work)]) assert.notDeepEqual(options.queryKey, changed.queryKey);
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(rendered(undefined, { code_review_id: review })));
  const observer = new QueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  await client.invalidateQueries({ queryKey: ["mnemonic"] });
  assert.equal(fetch.mock.callCount(), 0);
  await fetchFreshRenderedPrompt(client, project, "recall-pointer", work, review);
  assert.equal(fetch.mock.callCount(), 1);
  const [url, init] = fetch.mock.calls[0].arguments;
  assert.equal(url, `/api/mnemonic/projects/${project}/prompts/recall-pointer/render`);
  assert.equal(init.method, "POST");
  assert.deepEqual(JSON.parse(init.body), { work_item_id: work, code_review_id: review });
  assert.ok(init.signal instanceof AbortSignal);
  assert.equal(options.gcTime, 0);
});

test("shared render reads deduplicate while each later clipboard action fetches new instructions", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  let calls = 0;
  const held = Promise.withResolvers();
  const fetch = t.mock.method(globalThis, "fetch", () => { calls++; return held.promise; });
  const options = renderedPromptOptions(project, "recall-pointer", work);
  const first = client.fetchQuery(options);
  const second = client.fetchQuery(options);
  assert.equal(calls, 1);
  held.resolve(Response.json(rendered("Shared read.")));
  assert.deepEqual(await Promise.all([first, second]), ["Shared read.", "Shared read."]);
  fetch.mock.mockImplementation(async () => { calls++; return Response.json(rendered(`Instructions ${calls}.`)); });
  assert.equal(await fetchFreshRenderedPrompt(client, project, "recall-pointer", work), "Instructions 2.");
  assert.equal(await fetchFreshRenderedPrompt(client, project, "recall-pointer", work), "Instructions 3.");
});

test("simultaneous fresh render actions supersede older transports without accepting stale instructions", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const requests = [];
  t.mock.method(globalThis, "fetch", (_url, init) => new Promise((resolve) => requests.push({ signal: init.signal, resolve })));
  const first = fetchFreshRenderedPrompt(client, project, "recall-pointer", work);
  const second = fetchFreshRenderedPrompt(client, project, "recall-pointer", work);
  const settled = Promise.allSettled([first, second]);
  await setImmediate();
  assert.equal(requests.filter((request) => !request.signal.aborted).length, 1);
  for (const request of requests) request.resolve(Response.json(rendered()));
  const results = await settled;
  assert.equal(results[0].status, "rejected");
  assert.ok(results[0].reason instanceof CancelledError);
  assert.equal(results[1].status, "fulfilled");
  assert.equal(results[1].value, "Current rendered instructions.");
});

test("cancelled render actions reject retained cache and a helper result without its own transport", async (t) => {
  const client = createDashboardQueryClient();
  const options = renderedPromptOptions(project, "recall-pointer", work);
  client.setQueryData(options.queryKey, "Old cached instructions.");
  const observer = new QueryObserver(client, options);
  const stop = observer.subscribe(() => {});
  t.after(() => { stop(); client.clear(); });
  const held = Promise.withResolvers();
  let signal;
  t.mock.method(globalThis, "fetch", (_url, init) => { signal = init.signal; return held.promise; });
  const pending = fetchFreshRenderedPrompt(client, project, "recall-pointer", work);
  const rejected = assert.rejects(pending, CancelledError);
  observer.setOptions({ queryKey: ["disabled", "render"], enabled: false });
  assert.equal(signal.aborted, true);
  await rejected;
  held.resolve(Response.json(rendered("Late cancelled instructions.")));
  t.mock.method(client, "fetchQuery", async () => "Old cached instructions.");
  await assert.rejects(fetchFreshRenderedPrompt(client, project, "recall-pointer", work), CancelledError);
});

test("render failures and wrong-scope responses never retry or fall back to cached instructions", async (t) => {
  const client = createDashboardQueryClient();
  t.after(() => client.clear());
  const fetch = t.mock.method(globalThis, "fetch", async () => Response.json(rendered()));
  await fetchFreshRenderedPrompt(client, project, "recall-pointer", work);
  for (const response of [Response.json({ detail: "Prompt unavailable." }, { status: 503 }),
    ...[{ project_id: work }, { prompt_id: "request-human-review" }, { work_item_id: project }, { code_review_id: review }]
      .map((changes) => Response.json(rendered(undefined, changes)))]) {
    const before = fetch.mock.callCount();
    fetch.mock.mockImplementation(async () => response);
    await assert.rejects(fetchFreshRenderedPrompt(client, project, "recall-pointer", work), /Prompt unavailable|invalid prompt/);
    assert.equal(fetch.mock.callCount(), before + 1);
  }
});
