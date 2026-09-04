import assert from "node:assert/strict";
import test from "node:test";
import {
  PROJECT_SHORTCUT_LIMIT,
  projectShortcutIndex,
  projectShortcutKey
} from "../lib/project-shortcuts.ts";

test("the number row binds the first ten projects and no more", () => {
  assert.equal(PROJECT_SHORTCUT_LIMIT, 10);
  assert.equal(projectShortcutKey(0), "1");
  assert.equal(projectShortcutKey(8), "9");
  // The tenth project takes 0, where the number row itself puts it.
  assert.equal(projectShortcutKey(9), "0");
  assert.equal(projectShortcutKey(10), null);
  assert.equal(projectShortcutKey(-1), null);
  assert.equal(projectShortcutKey(1.5), null);
});

test("a bound digit resolves to its project and every other key is left alone", () => {
  for (let index = 0; index < PROJECT_SHORTCUT_LIMIT; index += 1) {
    assert.equal(projectShortcutIndex(projectShortcutKey(index)), index);
  }
  assert.equal(projectShortcutIndex("1"), 0);
  assert.equal(projectShortcutIndex("9"), 8);
  assert.equal(projectShortcutIndex("0"), 9);
  for (const key of ["", " ", "10", "01", "!", "a", "F1", "ArrowLeft", "Escape", "０"]) {
    assert.equal(projectShortcutIndex(key), null, `${key} must not select a project`);
  }
});
