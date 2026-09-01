import assert from "node:assert/strict";
import test from "node:test";
import { childSearchParams, workSearchParams } from "../lib/work-item-search.ts";

test("work search includes the explicit full view and defaults to lexical retrieval", () => {
  const params = workSearchParams({ status: "open", sort: "created", limit: 20, offset: 0, query: "database migration" });
  assert.equal(params.toString(), "status=open&sort=created&view=full&limit=20&offset=0&q=database+migration");
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
  const browse = workSearchParams({ status: "open", sort: "updated", limit: 20, offset: 20, query: "" });
  assert.equal(browse.toString(), "status=open&sort=updated&view=roots&limit=20&offset=20");
  const active = workSearchParams({ status: "active", sort: "priority", limit: 20, offset: 0, query: "" });
  assert.equal(active.toString(), "status=active&sort=priority&view=roots&limit=20&offset=0");
  assert.equal(childSearchParams({ status: "promoted", sort: "created", limit: 50, offset: 100 }).toString(), "status=promoted&sort=created&limit=50&offset=100");
  assert.equal(childSearchParams({ status: "dropped", sort: "updated", limit: 50, offset: 0 }).toString(), "status=dropped&sort=updated&limit=50&offset=0");
});
