import assert from "node:assert/strict";
import test from "node:test";
import { recallPointer } from "../lib/recall-pointer.ts";

const projectId = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const handoffId = "f1cf3691-7d28-4716-94a9-4867b341a685";
const handoff = (title) => ({ id: handoffId, project_id: projectId, title });

test("recall pointers identify a hand-off without embedding its body", () => {
  assert.equal(
    recallPointer(handoff("Investigate dashboard proxy")),
    `Recall the Mnemonic hand-off "Investigate dashboard proxy" (project_id ${projectId}, handoff_id ${handoffId}) using recall_handoff, then summarise it and wait for my direction.`
  );
});

test("recall pointers preserve double quotes in titles", () => {
  assert.equal(
    recallPointer(handoff('Investigate "trusted origin" errors')),
    `Recall the Mnemonic hand-off "Investigate "trusted origin" errors" (project_id ${projectId}, handoff_id ${handoffId}) using recall_handoff, then summarise it and wait for my direction.`
  );
});

test("recall pointers remain compact with a maximum-length title", () => {
  const pointer = recallPointer(handoff("x".repeat(200)));
  assert.equal(pointer.length < 500, true);
  assert.equal(pointer.endsWith("\n"), false);
});
