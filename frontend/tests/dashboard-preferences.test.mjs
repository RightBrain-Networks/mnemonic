import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import test from "node:test";
import {
  dashboardLibraryToolsPreference,
  libraryToolsInitializationScript,
  dashboardSortPreference,
  dashboardStatusPreference,
  dashboardStorageKeys,
  dashboardWorkSplitPreference
} from "../lib/dashboard-preferences.ts";

test("dashboard preferences use stable local-storage keys", () => {
  assert.deepEqual(dashboardStorageKeys, {
    project: "mnemonic.project",
    status: "mnemonic.status",
    sort: "mnemonic.sort",
    libraryTools: "mnemonic.library-tools",
    workSplit: "mnemonic.work-split"
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

test("the library tools default open and restore only an explicit closed preference", () => {
  assert.equal(dashboardLibraryToolsPreference("closed"), false);
  for (const value of [null, "", "open", "OPEN", "false"]) {
    assert.equal(dashboardLibraryToolsPreference(value), true);
  }
});

function initializedLibraryTools(saved = null, storageError = false) {
  const documentElement = { dataset: {} };
  runInNewContext(libraryToolsInitializationScript, {
    document: { documentElement },
    localStorage: {
      getItem() {
        if (storageError) throw new Error("storage unavailable");
        return saved;
      }
    }
  });
  return documentElement.dataset.libraryTools;
}

test("the pre-paint initializer restores closed without changing the open default", () => {
  assert.equal(initializedLibraryTools("closed"), "closed");
  for (const value of [null, "", "open", "invalid"]) {
    assert.equal(initializedLibraryTools(value), "open");
  }
  assert.equal(initializedLibraryTools("closed", true), "open");
});

test("the work-surface split restores a bounded number and otherwise stays uncustomised", () => {
  assert.equal(dashboardWorkSplitPreference("48"), 48);
  assert.equal(dashboardWorkSplitPreference("12"), 20);
  assert.equal(dashboardWorkSplitPreference("80"), 70);
  for (const value of [null, "", "half", "Infinity"]) {
    assert.equal(dashboardWorkSplitPreference(value), null);
  }
});
