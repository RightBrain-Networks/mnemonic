import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_HIERARCHY_DEPTH,
  hierarchyGuardReason,
  relationshipConflictMessage,
  relationshipGroup,
  relationshipPreview
} from "../lib/work-relationships.ts";

const adjacent = (relationship_type, direction) => ({
  relationship: { relationship_type },
  direction
});

test("relationship groups use endpoint-relative human language", () => {
  assert.equal(relationshipGroup(adjacent("blocks", "incoming")), "Blocked by");
  assert.equal(relationshipGroup(adjacent("blocks", "outgoing")), "Blocks");
  assert.equal(relationshipGroup(adjacent("parent-child", "incoming")), "Parent");
  assert.equal(relationshipGroup(adjacent("parent-child", "outgoing")), "Children");
  assert.equal(relationshipGroup(adjacent("discovered-from", "outgoing")), "Discovered from");
  assert.equal(relationshipGroup(adjacent("discovered-from", "incoming")), "Discovered work");
  assert.equal(relationshipGroup(adjacent("duplicate-of", "incoming")), "Duplicate of");
  assert.equal(relationshipGroup(adjacent("related", "undirected")), "Related");
});

test("relationship previews make stored source and target direction explicit", () => {
  assert.equal(relationshipPreview("blocks", "outgoing", "Build API", "Ship UI"), "Build API blocks Ship UI.");
  assert.equal(relationshipPreview("blocks", "incoming", "Build API", "Ship UI"), "Ship UI blocks Build API.");
  assert.equal(relationshipPreview("parent-child", "outgoing", "Epic", "Task"), "Epic is the parent of Task.");
  assert.equal(relationshipPreview("discovered-from", "outgoing", "Follow-up", "Investigation"), "Follow-up was discovered from Investigation.");
  assert.equal(relationshipPreview("related", "incoming", "One", "Two"), "One and Two are related.");
});

test("graph conflicts and corrupt hierarchy fallbacks remain actionable", () => {
  assert.match(relationshipConflictMessage("relationship_cycle"), /cycle/i);
  assert.match(relationshipConflictMessage("parent_already_set"), /already has a parent/i);
  assert.equal(relationshipConflictMessage("relationship_exists"), null);
  assert.match(hierarchyGuardReason("repeat", new Set(["repeat"]), 2), /cycle/i);
  assert.match(hierarchyGuardReason("deep", new Set(), MAX_HIERARCHY_DEPTH), /bounded hierarchy depth/i);
  assert.equal(hierarchyGuardReason("safe", new Set(), MAX_HIERARCHY_DEPTH - 1), null);
});
