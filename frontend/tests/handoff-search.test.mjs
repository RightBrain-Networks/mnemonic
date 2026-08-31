import assert from "node:assert/strict";
import test from "node:test";
import { handoffSearchParams } from "../lib/handoff-search.ts";

test("ordinary hand-off searches omit semantic mode by default", () => {
  const params = handoffSearchParams({
    status: "open",
    limit: 20,
    offset: 0,
    query: "database migration"
  });

  assert.equal(params.toString(), "status=open&limit=20&offset=0&q=database+migration");
  assert.equal(params.has("semantic"), false);
});

test("semantic searches trim the query and opt in explicitly", () => {
  const params = handoffSearchParams({
    status: "done",
    limit: 20,
    offset: 40,
    query: "  database migration  ",
    semantic: true
  });

  assert.equal(params.get("q"), "database migration");
  assert.equal(params.get("semantic"), "true");
  assert.equal(params.get("status"), "done");
  assert.equal(params.get("limit"), "20");
  assert.equal(params.get("offset"), "40");
});

test("semantic mode is not sent without a nonblank query", () => {
  const params = handoffSearchParams({
    status: "all",
    limit: 20,
    offset: 0,
    query: " \n\t ",
    semantic: true
  });

  assert.equal(params.has("q"), false);
  assert.equal(params.has("semantic"), false);
  assert.equal(params.toString(), "status=all&limit=20&offset=0");
});
