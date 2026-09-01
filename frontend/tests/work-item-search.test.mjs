import assert from "node:assert/strict";
import test from "node:test";
import { childSearchParams, workSearchParams } from "../lib/work-item-search.ts";

test("work search includes the explicit all-project view and defaults to lexical retrieval", () => {
  const params = workSearchParams({ status: "open", limit: 20, offset: 0, query: "database migration" });
  assert.equal(params.toString(), "status=open&view=all&limit=20&offset=0&q=database+migration");
  assert.equal(params.has("semantic"), false);
});

test("semantic work search trims and requires a nonblank query", () => {
  const semantic = workSearchParams({ status: "done", limit: 20, offset: 40, query: "  durable context  ", semantic: true });
  assert.equal(semantic.get("q"), "durable context");
  assert.equal(semantic.get("semantic"), "true");
  assert.equal(semantic.get("view"), "all");

  const blank = workSearchParams({ status: "all", limit: 20, offset: 0, query: " \n\t ", semantic: true });
  assert.equal(blank.toString(), "status=all&view=roots&limit=20&offset=0");
});

test("ordinary browse pages structural roots and child pages inherit filters", () => {
  const browse = workSearchParams({ status: "open", limit: 20, offset: 20, query: "" });
  assert.equal(browse.toString(), "status=open&view=roots&limit=20&offset=20");
  assert.equal(childSearchParams({ status: "promoted", limit: 50, offset: 100 }).toString(), "status=promoted&limit=50&offset=100");
});
