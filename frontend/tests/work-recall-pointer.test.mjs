import assert from "node:assert/strict";
import test from "node:test";
import { workRecallPointer } from "../lib/work-recall-pointer.ts";

const projectId = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const workId = "7a5dc555-0a6d-4f92-9678-1647524827c8";

test("canonical recall pointers identify durable work without embedding checkpoint text", () => {
  const pointer = workRecallPointer({ work_item: { id: workId, project_id: projectId, title: "Investigate proxy policy" } });
  assert.equal(pointer, `Recall the Mnemonic work item "Investigate proxy policy" (project_id ${projectId}, work_item_id ${workId}) using recall_work, then summarise its current context and wait for my direction.`);
});
