import assert from "node:assert/strict";
import test from "node:test";
import {
  isBlockingProjectSettingsLoad,
  isCurrentProjectSettingsLoad
} from "../lib/project-settings.ts";

const projectId = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const otherProjectId = "f1cf3691-7d28-4716-94a9-4867b341a685";

test("initial and project-switch settings loads block controls", () => {
  assert.equal(isBlockingProjectSettingsLoad(projectId, null), true);
  assert.equal(isBlockingProjectSettingsLoad(projectId, {
    project_id: otherProjectId,
    recall_pointer_template: "Other project template"
  }), true);
});

test("same-project background refreshes keep loaded controls usable", () => {
  assert.equal(isBlockingProjectSettingsLoad(projectId, {
    project_id: projectId,
    recall_pointer_template: "Recall $WORK_ITEM_ID"
  }), false);
  assert.equal(isBlockingProjectSettingsLoad(projectId, {
    project_id: projectId,
    recall_pointer_template: null
  }), false);
});

test("only the latest non-aborted settings load may update state", () => {
  assert.equal(isCurrentProjectSettingsLoad(4, 4, false), true);
  assert.equal(isCurrentProjectSettingsLoad(3, 4, false), false);
  assert.equal(isCurrentProjectSettingsLoad(4, 4, true), false);
});
