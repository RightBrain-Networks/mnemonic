import assert from "node:assert/strict";
import test from "node:test";
import { migrationWarning, normalizedTags } from "../lib/work-item-view.ts";

test("legacy snapshots receive an explicit provenance limitation warning", () => {
  const warning = migrationWarning("legacy-handoff-snapshot");
  assert.match(warning, /preserved exactly/);
  assert.match(warning, /did not record who made later prompt edits/);
  assert.equal(migrationWarning(null), null);
});

test("dashboard checkpoint tags are normalized, deduplicated, bounded, and ordered", () => {
  assert.deepEqual(normalizedTags(" API,phase-1, api,  Ready  "), ["api", "phase-1", "ready"]);
  assert.equal(normalizedTags(Array.from({ length: 25 }, (_, index) => `tag-${index}`).join(",")).length, 20);
});
