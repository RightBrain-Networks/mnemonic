import assert from "node:assert/strict";
import test from "node:test";
import {
  dashboardSortPreference,
  dashboardStatusPreference,
  dashboardStorageKeys
} from "../lib/dashboard-preferences.ts";

test("dashboard preferences use stable local-storage keys", () => {
  assert.deepEqual(dashboardStorageKeys, {
    project: "mnemonic.project",
    status: "mnemonic.status",
    sort: "mnemonic.sort"
  });
});

test("dashboard preferences restore valid status and sort selections", () => {
  for (const status of ["pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"]) {
    assert.equal(dashboardStatusPreference(status), status);
  }
  assert.equal(dashboardStatusPreference("open"), "pending");
  for (const sort of ["updated", "created", "priority"]) {
    assert.equal(dashboardSortPreference(sort), sort);
  }
});

test("missing or invalid preferences fall back to dashboard defaults", () => {
  for (const value of [null, "", "closed", "UPDATED"]) {
    assert.equal(dashboardStatusPreference(value), "pending");
    assert.equal(dashboardSortPreference(value), "updated");
  }
});
