import assert from "node:assert/strict";
import { runInNewContext } from "node:vm";
import test from "node:test";
import {
  resolvedTheme,
  themeInitializationScript,
  themePreference,
  themeStorageKey
} from "../lib/theme-preference.ts";

test("theme preferences use a stable local-storage key and default to auto", () => {
  assert.equal(themeStorageKey, "mnemonic.theme");
  for (const preference of ["auto", "dark", "light"]) {
    assert.equal(themePreference(preference), preference);
  }
  for (const value of [null, undefined, "", "system", "DARK"]) {
    assert.equal(themePreference(value), "auto");
  }
});

test("auto follows the system while explicit themes override it", () => {
  assert.equal(resolvedTheme("auto", true), "dark");
  assert.equal(resolvedTheme("auto", false), "light");
  assert.equal(resolvedTheme("dark", false), "dark");
  assert.equal(resolvedTheme("light", true), "light");
});

function runInitialization({ saved = null, systemDark = false, storageError = false } = {}) {
  const documentElement = { dataset: {}, style: {} };
  runInNewContext(themeInitializationScript, {
    document: { documentElement },
    localStorage: {
      getItem() {
        if (storageError) throw new Error("storage unavailable");
        return saved;
      }
    },
    window: { matchMedia: () => ({ matches: systemDark }) }
  });
  return documentElement;
}

test("the pre-paint initializer restores manual choices", () => {
  assert.equal(runInitialization({ saved: "dark" }).dataset.theme, "dark");
  assert.equal(runInitialization({ saved: "light", systemDark: true }).dataset.theme, "light");
});

test("the pre-paint initializer follows the system for missing or invalid choices", () => {
  assert.equal(runInitialization({ systemDark: true }).dataset.theme, "dark");
  assert.equal(runInitialization({ saved: "invalid" }).dataset.theme, "light");
  assert.equal(runInitialization({ storageError: true, systemDark: true }).dataset.theme, "dark");
});
