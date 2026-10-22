import assert from "node:assert/strict";
import { setImmediate } from "node:timers/promises";
import test from "node:test";
import { QueryObserver } from "@tanstack/react-query";
import { MutationIntentRegistry, mutationWorkKey } from "../lib/mutation-intent.ts";
import { MUTATION_KINDS } from "../lib/mutation-responses.ts";
import { createDashboardQueryClient } from "../lib/query-client.ts";
import { queryKeys } from "../lib/query-keys.ts";
import { connectWorkMutationQueries, invalidateConfirmedWorkMutation } from "../lib/work-mutation-queries.ts";
import { project, work, followWork, operation, workItem } from "./phase12-fixtures.mjs";

const target = "99999999-9999-4999-8999-999999999999";
const unrelated = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const detail = (projectId = project, workId = work, facet = "context") =>
  [...queryKeys.project(projectId), "work-detail", workId, facet];
const roots = ["work", "tasks", "work-detail", "attention-count", "attention", "report-count", "reports", "settings", "knowledge"];

function setup(t, subscriptions = []) {
  const client = createDashboardQueryClient();
  t.after(() => { for (const unsubscribe of subscriptions) unsubscribe(); client.clear(); });
  for (const projectId of [project, target, unrelated]) {
    for (const root of roots) {
      client.setQueryData([...queryKeys.project(projectId), root, work], ["cached"]);
    }
    for (const workId of [work, followWork]) {
      for (const facet of ["context", "history", "events", "relationships", "reviews", "evidence"]) {
        client.setQueryData(detail(projectId, workId, facet), ["cached"]);
      }
    }
  }
  client.setQueryData(queryKeys.projects, ["cached"]);
  return client;
}

function confirmation(kind, result = {}, conflictKeys = [mutationWorkKey(project, work)]) {
  return { kind, intent: { kind, projectId: project, conflictKeys }, result };
}

function deletionInput() {
  return {
    kind: "delete_work", slot: `delete:${project}:${work}`, projectId: project,
    conflictKeys: [mutationWorkKey(project, work)], method: "POST",
    path: `/projects/${project}/work-items/${work}/delete`,
    payload: { expected_version: 2, actor: { actor_client: "dashboard", actor_session_id: "tab-1" } }
  };
}

function deletionResponse(overrides = {}) {
  return Response.json({ deleted: true, project_id: project, work_item_id: work, version: 3, ...overrides });
}

function expectScope(client, projectId, expectedRoots) {
  for (const root of roots) {
    assert.equal(client.getQueryState([...queryKeys.project(projectId), root, work]).isInvalidated,
      expectedRoots.includes(root), `${projectId}: ${root}`);
  }
  assert.equal(client.getQueryState(queryKeys.projects).isInvalidated, false);
}

test("every protected work operation refreshes its declared reads and keeps unrelated projects cached", async (t) => {
  const basic = ["work", "tasks", "work-detail", "attention-count", "attention"];
  const closeout = [...basic, "report-count", "reports"];
  const cases = {
    create_work: [{ work_item: { project_id: project }, initial_relationships: [] }, basic],
    add_checkpoint: [{}, basic],
    append_event: [{ project_id: project }, basic],
    add_relationship: [{ relationship: { project_id: project } }, basic],
    update_work: [{ project_id: project }, closeout],
    defer_work: [{ project_id: project }, basic],
    complete_work: [{ work_item: { project_id: project } }, closeout],
    respond_to_work_follow_up: [{ follow_up: { project_id: project } }, basic],
    delete_work: [{ project_id: project }, closeout],
    move_work: [{ source_project_id: project, target_project_id: project, work_item: { project_id: project } }, closeout],
    remove_relationship: [{ project_id: project }, basic],
    resolve_human_input: [{ project_id: project }, [...basic, "knowledge"]],
    merge_work: [{ source_work_item: { project_id: project }, destination_work_item: { project_id: project } }, closeout],
    dismiss_job_completion_report: [{ project_id: project }, ["work-detail", "report-count", "reports"]],
    create_job_completion_report_follow_up: [{ work_item: { project_id: project } }, closeout]
  };
  assert.deepEqual(Object.keys(cases).sort(), [...MUTATION_KINDS].sort());
  for (const [kind, [result, expected]] of Object.entries(cases)) {
    await t.test(kind, async (t) => {
      const client = setup(t);
      await invalidateConfirmedWorkMutation(client, confirmation(kind, result));
      expectScope(client, project, expected);
      expectScope(client, target, []);
      expectScope(client, unrelated, []);
      assert.equal(client.getQueryState(detail(project, followWork, "relationships")).isInvalidated, true,
        "cached counterpart metadata in another work detail is refreshed");
    });
  }
});

test("only strict decoded success reconciles reads and recovery retries preserve the frozen request", async (t) => {
  const client = setup(t);
  const requests = [];
  const confirmed = [], recovered = [];
  let uuids = 0;
  const registry = new MutationIntentRegistry(async (url, init) => {
    requests.push({ url, method: init.method, body: init.body });
    if (requests.length === 1) return Response.json({ detail: "Lost response" }, { status: 502 });
    if (requests.length === 2) return deletionResponse({ version: 999 });
    return deletionResponse();
  }, () => { uuids += 1; return operation; });
  const disconnect = connectWorkMutationQueries(client, registry);
  t.after(disconnect);
  registry.subscribeConfirmed((value) => confirmed.push(value));
  registry.subscribeRecovered((value) => recovered.push(value));
  const input = deletionInput();
  await assert.rejects(registry.execute(input), { state: "unresolved" });
  await assert.rejects(registry.retry(input.slot), { state: "unresolved" });
  assert.equal(registry.get(input.slot).state, "unresolved");
  assert.equal(confirmed.length, 0);
  assert.equal(recovered.length, 0);
  expectScope(client, project, []);
  await registry.retry(input.slot);
  expectScope(client, project, ["work", "tasks", "work-detail", "attention-count", "attention", "report-count", "reports"]);
  assert.equal(registry.get(input.slot), undefined);
  assert.equal(confirmed.length, 1);
  assert.equal(recovered.length, 1);
  assert.equal(confirmed[0].result.version, 3);
  assert.equal("body" in confirmed[0].intent, false);
  assert.equal("operationId" in confirmed[0].intent, false);
  assert.equal(uuids, 1);
  assert.deepEqual(requests[1], requests[0]);
  assert.deepEqual(requests[2], requests[0]);
});

test("initial success emits confirmation without changing retry-only recovery callbacks", async (t) => {
  const client = setup(t);
  const registry = new MutationIntentRegistry(async () => deletionResponse(), () => operation);
  const confirmations = [], recoveries = [];
  const disconnect = connectWorkMutationQueries(client, registry);
  registry.subscribeConfirmed((value) => confirmations.push(value));
  registry.subscribeRecovered((value) => recoveries.push(value));
  await registry.execute(deletionInput());
  assert.equal(confirmations.length, 1);
  assert.equal(recoveries.length, 0);
  assert.equal(client.getQueryState(detail()).isInvalidated, true);
  disconnect();
  client.setQueryData(detail(), ["refreshed"]);
  await registry.execute(deletionInput());
  assert.equal(client.getQueryState(detail()).isInvalidated, false, "unsubscribed bridge stops observing");
});

test("definitive rejection and operation safety conflict never confirm or invalidate cached reads", async (t) => {
  const cases = [
    [422, { detail: [{ type: "value_error", loc: ["body", "expected_version"], msg: "Invalid version" }] }],
    [409, { detail: { code: "client_operation_conflict", message: "The operation ID has another request", context: {} } }]
  ];
  for (const [status, body] of cases) {
    await t.test(String(status), async (t) => {
      const client = setup(t), confirmations = [];
      const registry = new MutationIntentRegistry(async () => Response.json(body, { status }), () => operation);
      t.after(connectWorkMutationQueries(client, registry));
      registry.subscribeConfirmed((value) => confirmations.push(value));
      await assert.rejects(registry.execute(deletionInput()));
      assert.equal(confirmations.length, 0);
      expectScope(client, project, []);
      assert.deepEqual(client.getQueryData(detail()), ["cached"]);
      assert.equal(registry.get(deletionInput().slot)?.state, status === 409 ? "safety_conflict" : undefined);
    });
  }
});

test("decoded move invalidates cached source and destination reads even when neither project is selected", async (t) => {
  const client = setup(t);
  const registry = new MutationIntentRegistry(async () => Response.json({
    source_project_id: project, target_project_id: target, preserved_status: "pending",
    work_item: { ...workItem, project_id: target, status: "pending", version: 3 }
  }), () => operation);
  t.after(connectWorkMutationQueries(client, registry));
  await registry.execute({
    kind: "move_work", slot: "move", projectId: project,
    // Exercise decoded-result scope expansion independently of conflict keys.
    conflictKeys: [mutationWorkKey(project, work)], method: "POST",
    path: `/projects/${project}/work-items/${work}/move`,
    expectedSourceWorkStatus: "pending",
    payload: { target_project_id: target, expected_version: 2,
      actor: { actor_client: "dashboard", actor_session_id: "tab-1" } }
  });
  for (const projectId of [project, target]) {
    expectScope(client, projectId, ["work", "tasks", "work-detail", "attention-count", "attention", "report-count", "reports"]);
  }
  expectScope(client, unrelated, []);
});

test("merge refreshes both work endpoints and relationships refresh both frozen project scopes", async (t) => {
  const client = setup(t);
  await invalidateConfirmedWorkMutation(client, confirmation("merge_work", {
    source_work_item: { id: work, project_id: project },
    destination_work_item: { id: followWork, project_id: project }
  }, [mutationWorkKey(project, work), mutationWorkKey(project, followWork)]));
  for (const workId of [work, followWork]) {
    for (const facet of ["context", "history", "events", "relationships", "reviews", "evidence"]) {
      assert.equal(client.getQueryState(detail(project, workId, facet)).isInvalidated, true);
    }
  }
  expectScope(client, unrelated, []);
  await invalidateConfirmedWorkMutation(client, confirmation("remove_relationship", { project_id: project },
    [mutationWorkKey(project, work), mutationWorkKey(target, followWork)]));
  expectScope(client, target, ["work", "tasks", "work-detail", "attention-count", "attention"]);
});

test("confirmation marks guarded context stale and cancels older active history reads", async (t) => {
  const subscriptions = [];
  const client = setup(t, subscriptions);
  const reads = [], late = Promise.withResolvers(), fresh = Promise.withResolvers();
  const contextKey = detail();
  const eventsKey = detail(project, work, "events");
  const guarded = new QueryObserver(client, { queryKey: contextKey, enabled: false,
    queryFn: async () => { throw new Error("The placement coordinator owns this read"); } });
  const events = new QueryObserver(client, { queryKey: eventsKey,
    queryFn: ({ signal }) => {
      reads.push(signal);
      return reads.length === 1 ? late.promise : fresh.promise;
    } });
  subscriptions.push(guarded.subscribe(() => {}), events.subscribe(() => {}));
  const explicit = events.refetch();
  await setImmediate();
  const invalidation = invalidateConfirmedWorkMutation(client,
    confirmation("delete_work", { project_id: project }));
  assert.equal(reads.length, 2);
  assert.equal(reads[0].aborted, true);
  assert.equal(client.getQueryState(contextKey).isInvalidated, true);
  assert.deepEqual(guarded.getCurrentResult().data, ["cached"]);
  fresh.resolve(["fresh"]);
  await Promise.all([explicit, invalidation]);
  assert.deepEqual(events.getCurrentResult().data, ["fresh"]);
  late.resolve(["obsolete"]);
  await setImmediate();
  assert.deepEqual(events.getCurrentResult().data, ["fresh"]);
});
