import assert from "node:assert/strict";
import test from "node:test";
import { canonicalDashboardPath, dashboardPaths, parseDashboardSearch, savedWorkRedirect, stringifyDashboardSearch } from "../lib/dashboard-routes.ts";

test("dashboard routes expose the twelve current paths and exclude the retired settings root", () => {
  assert.equal(new Set(dashboardPaths).size, 12);
  assert.equal(dashboardPaths.includes("/settings"), false);
  assert.equal(dashboardPaths.includes("/missing"), false);
});

test("known trailing slash routes canonicalize without admitting unknown paths", () => {
  for (const path of dashboardPaths) {
    assert.equal(canonicalDashboardPath(path === "/" ? path : `${path}/`), path);
  }
  assert.equal(canonicalDashboardPath("/settings/"), "/settings/");
  assert.equal(canonicalDashboardPath("/missing/"), "/missing/");
});

test("saved root work links carry their full query to the work library", () => {
  const query = "?work=123&project=456&review=1&tag=one&tag=two&note=hello%20world";
  assert.equal(savedWorkRedirect("/", query), `/work-items${query}`);
  assert.equal(savedWorkRedirect("/", "?project=456"), null);
  assert.equal(savedWorkRedirect("/", "?work=123&work=456"), null);
  assert.equal(savedWorkRedirect("/artifacts", query), null);
});

test("router search serialization keeps strings, empty values and repeated parameters", () => {
  const query = "?project=123&work=001&review=1&tag=one&tag=two&empty=&bool=false&json=%7B%7D";
  const parsed = parseDashboardSearch(query);
  assert.equal(parsed.work, "001");
  assert.equal(parsed.review, "1");
  assert.equal(parsed.bool, "false");
  assert.equal(parsed.json, "{}");
  assert.deepEqual(parsed.tag, ["one", "two"]);
  assert.equal(stringifyDashboardSearch(parsed), query);
  assert.equal(stringifyDashboardSearch({}), "");
  assert.equal(parseDashboardSearch("?__proto__=value").__proto__, "value");
});
