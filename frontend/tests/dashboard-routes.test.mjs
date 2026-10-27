import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { canonicalDashboardPath, dashboardPaths, parseDashboardSearch, savedWorkRedirect, stringifyDashboardSearch } from "../lib/dashboard-routes.ts";

test("dashboard routes expose the fifteen current paths and exclude the retired settings root", () => {
  assert.equal(new Set(dashboardPaths).size, 15);
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

test("nginx serves every dashboard route directly while unknown knowledge paths stay closed", async () => {
  const nginx = await readFile(new URL("../nginx.conf", import.meta.url), "utf8");
  const routeLine = nginx.split("\n").find((line) => line.includes("location ~ ^/(?:work-items"));
  const pattern = new RegExp(routeLine.trim().slice("location ~ ".length, -2).trim());
  for (const path of dashboardPaths.filter((path) => path !== "/")) {
    assert.ok(pattern.test(path), `${path} must support a direct page load`);
    assert.ok(pattern.test(`${path}/`), `${path}/ must support a direct page load`);
  }
  assert.equal(pattern.test("/knowledge/private"), false);
  assert.equal(pattern.test("/settings"), false);
});
