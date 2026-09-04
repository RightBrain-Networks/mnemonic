import assert from "node:assert/strict";
import test from "node:test";
import {
  PROJECT_SHORTCUT_LIMIT,
  projectShortcutIndex,
  projectShortcutKey,
  projectShortcutOptionLabel
} from "../lib/project-shortcuts.ts";

test("the function row binds the first twelve projects and no more", () => {
  assert.equal(PROJECT_SHORTCUT_LIMIT, 12);
  assert.equal(projectShortcutKey(0), "F1");
  assert.equal(projectShortcutKey(11), "F12");
  assert.equal(projectShortcutKey(12), null);
  assert.equal(projectShortcutKey(-1), null);
  assert.equal(projectShortcutKey(1.5), null);
});

test("every bound option names its own key and the rest stay plain", () => {
  assert.equal(projectShortcutOptionLabel("Mnemonic", 0), "F1 · Mnemonic");
  assert.equal(projectShortcutOptionLabel("Mnemonic", 11), "F12 · Mnemonic");
  assert.equal(projectShortcutOptionLabel("Mnemonic", 12), "Mnemonic");
});

test("a bound key resolves to its project and every other key is left alone", () => {
  for (let index = 0; index < PROJECT_SHORTCUT_LIMIT; index += 1) {
    assert.equal(projectShortcutIndex(`F${index + 1}`), index);
  }
  for (const key of ["F0", "F13", "F20", "f1", "F", "F1 ", "1", "Escape", "ArrowLeft", ""]) {
    assert.equal(projectShortcutIndex(key), null, `${key} must not select a project`);
  }
});
