import assert from "node:assert/strict";
import test from "node:test";
import {
  HIERARCHY_FILTER_DEBOUNCE_MS,
  childSearchParams,
  scheduleHierarchyFilterCommit,
  workSearchParams
} from "../lib/work-item-search.ts";

test("work search includes the explicit full view and defaults to lexical retrieval", () => {
  const params = workSearchParams({ status: "pending", sort: "created", limit: 20, offset: 0, query: "database migration" });
  assert.equal(params.toString(), "status=pending&sort=created&view=full&limit=20&offset=0&q=database+migration");
  assert.equal(params.has("semantic"), false);
});

test("semantic work search trims and requires a nonblank query", () => {
  const semantic = workSearchParams({ status: "done", sort: "priority", limit: 20, offset: 40, query: "  durable context  ", semantic: true });
  assert.equal(semantic.get("q"), "durable context");
  assert.equal(semantic.get("semantic"), "true");
  assert.equal(semantic.get("view"), "full");
  assert.equal(semantic.get("sort"), "priority");

  const blank = workSearchParams({ status: "all", sort: "updated", limit: 20, offset: 0, query: " \n\t ", semantic: true });
  assert.equal(blank.toString(), "status=all&sort=updated&view=roots&limit=20&offset=0");
});

test("ordinary browse pages structural roots and child pages inherit filters", () => {
  const browse = workSearchParams({ status: "pending", sort: "updated", limit: 20, offset: 20, query: "" });
  assert.equal(browse.toString(), "status=pending&sort=updated&view=roots&limit=20&offset=20");
  const active = workSearchParams({ status: "active", sort: "priority", limit: 20, offset: 0, query: "" });
  assert.equal(active.toString(), "status=active&sort=priority&view=roots&limit=20&offset=0");
  assert.equal(childSearchParams({ status: "promoted", sort: "created", limit: 50, offset: 100 }).toString(), "status=promoted&sort=created&limit=50&offset=100");
  assert.equal(childSearchParams({ status: "dropped", sort: "updated", limit: 50, offset: 0 }).toString(), "status=dropped&sort=updated&limit=50&offset=0");
  assert.equal(childSearchParams({ status: "deferred", sort: "updated", limit: 50, offset: 0 }).toString(), "status=deferred&sort=updated&limit=50&offset=0");
  assert.equal(
    childSearchParams({
      status: "done",
      sort: "created",
      limit: 50,
      offset: 0,
      tag: " release ",
      sourceClient: " claude-code ",
      sourceSessionId: " session-7 "
    }).toString(),
    "status=done&sort=created&limit=50&offset=0&tag=release&source_client=claude-code&source_session_id=session-7"
  );
  assert.equal(
    childSearchParams({
      status: "all",
      sort: "created",
      limit: 50,
      offset: 0,
      tag: " ",
      sourceClient: "",
      sourceSessionId: "\n"
    }).toString(),
    "status=all&sort=created&limit=50&offset=0"
  );
});

test("rapid hierarchy-filter edits produce one request-driving committed key", () => {
  const tasks = [];
  const commits = [];
  const schedule = (callback, delay) => {
    const task = { callback, delay, cancelled: false };
    tasks.push(task);
    return () => { task.cancelled = true; };
  };
  const current = { tag: "", sourceClient: "", sourceSessionId: "" };
  let cancel = scheduleHierarchyFilterCommit({
    ...current,
    tag: "r"
  }, current, (next) => commits.push(next), schedule);
  cancel();
  cancel = scheduleHierarchyFilterCommit({
    ...current,
    tag: "re"
  }, current, (next) => commits.push(next), schedule);
  cancel();
  scheduleHierarchyFilterCommit({
    tag: " release ",
    sourceClient: " playwright-api ",
    sourceSessionId: " session-7 "
  }, current, (next) => commits.push(next), schedule);

  for (const task of tasks) {
    if (!task.cancelled) task.callback();
  }
  assert.deepEqual(tasks.map((task) => task.delay), [
    HIERARCHY_FILTER_DEBOUNCE_MS,
    HIERARCHY_FILTER_DEBOUNCE_MS,
    HIERARCHY_FILTER_DEBOUNCE_MS
  ]);
  assert.deepEqual(commits, [{
    tag: "release",
    sourceClient: "playwright-api",
    sourceSessionId: "session-7"
  }]);
});
