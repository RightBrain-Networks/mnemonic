import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_RECALL_POINTER_TEMPLATE,
  RECALL_POINTER_MACROS,
  expandRecallPointerTemplate,
  workRecallPointer
} from "../lib/work-recall-pointer.ts";

const projectId = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const workId = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const project = {
  name: "Mnemonic dashboard",
  slug: "mnemonic-dashboard"
};
const summary = {
  work_item: {
    id: workId,
    project_id: projectId,
    title: "Investigate proxy policy",
    summary: "Keep proxy behavior safe and predictable.",
    status: "open",
    priority: 17
  }
};

test("the default template preserves the canonical recall pointer", () => {
  assert.equal(
    DEFAULT_RECALL_POINTER_TEMPLATE,
    'Recall the Mnemonic work item "$WORK_ITEM_TITLE" (project_id $PROJECT_ID, work_item_id $WORK_ITEM_ID) using recall_work, then summarise its current context and wait for my direction.'
  );
  assert.equal(
    workRecallPointer(summary),
    `Recall the Mnemonic work item "Investigate proxy policy" (project_id ${projectId}, work_item_id ${workId}) using recall_work, then summarise its current context and wait for my direction.`
  );
});

test("macro metadata describes every supported legend entry", () => {
  assert.deepEqual(
    RECALL_POINTER_MACROS.map(({ macro }) => macro),
    [
      "$WORK_ITEM_TITLE",
      "$WORK_ITEM_SUMMARY",
      "$WORK_ITEM_STATUS",
      "$WORK_ITEM_PRIORITY",
      "$PROJECT_ID",
      "$PROJECT_NAME",
      "$PROJECT_SLUG",
      "$WORK_ITEM_ID"
    ]
  );
  assert.ok(RECALL_POINTER_MACROS.every(({ description }) => description.length > 0));
});

test("custom templates expand every supported macro", () => {
  const template = [
    "title=$WORK_ITEM_TITLE",
    "summary=$WORK_ITEM_SUMMARY",
    "status=$WORK_ITEM_STATUS",
    "priority=$WORK_ITEM_PRIORITY",
    "project_id=$PROJECT_ID",
    "project_name=$PROJECT_NAME",
    "project_slug=$PROJECT_SLUG",
    "work_item_id=$WORK_ITEM_ID"
  ].join("\n");
  assert.equal(
    workRecallPointer(summary, { template, project }),
    [
      "title=Investigate proxy policy",
      "summary=Keep proxy behavior safe and predictable.",
      "status=open",
      "priority=17",
      `project_id=${projectId}`,
      "project_name=Mnemonic dashboard",
      "project_slug=mnemonic-dashboard",
      `work_item_id=${workId}`
    ].join("\n")
  );
});

test("repeated macros are expanded at every occurrence", () => {
  assert.equal(
    workRecallPointer(summary, { template: "$WORK_ITEM_ID / $WORK_ITEM_ID / $PROJECT_ID" }),
    `${workId} / ${workId} / ${projectId}`
  );
});

test("unknown macros are preserved", () => {
  assert.equal(
    workRecallPointer(summary, { template: "$UNKNOWN_MACRO / $WORK_ITEM_ID_EXTRA / $WORK_ITEM_ID_suffix / $WORK_ITEM_ID" }),
    `$UNKNOWN_MACRO / $WORK_ITEM_ID_EXTRA / $WORK_ITEM_ID_suffix / ${workId}`
  );
});

test("expansion is one pass and keeps dollar signs in values literal", () => {
  const values = Object.fromEntries(
    RECALL_POINTER_MACROS.map(({ macro }) => [macro, `literal $& $$ $PROJECT_ID from ${macro}`])
  );
  assert.equal(
    expandRecallPointerTemplate("$WORK_ITEM_TITLE", values),
    "literal $& $$ $PROJECT_ID from $WORK_ITEM_TITLE"
  );
});
