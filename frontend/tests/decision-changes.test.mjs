import assert from "node:assert/strict";
import test from "node:test";
import { decodeDecisionChange, decodeDecisionRevisionChange, matchDecisionChangeMutation } from "../lib/decision-changes.ts";
import { classifyMutationResponse } from "../lib/mutation-responses.ts";
import { MutationIntentRegistry } from "../lib/mutation-intent.ts";

const project = "10000000-0000-4000-8000-000000000001";
const changeId = "20000000-0000-4000-8000-000000000001";
const work = "30000000-0000-4000-8000-000000000001";
const assessment = "40000000-0000-4000-8000-000000000001";
const operation = "50000000-0000-4000-8000-000000000001";
const now = "2026-10-24T12:00:00Z";
const change = { id: changeId, project_id: project, origin_reference: "ADR-00001-1",
  review_work_item_id: work, parent_change_id: null, scope_version: 2, acknowledged_scope_version: 2,
  review_work_project_id: project, review_closed: false, successor_change_id: null,
  assessment_count: 2, revision_count: 2, pending_revision_count: 0, created_at: now, updated_at: now };
const payload = { action: "acknowledge", change_id: changeId, expected_scope_version: 2,
  actor_client: "dashboard", actor_session_id: "native-tab" };
const request = { kind: "decision_change_update", method: "POST", path: `/projects/${project}/knowledge/changes`,
  body: JSON.stringify({ ...payload, client_operation_id: operation }), operationId: operation };

test("change reads bind project identity and keep acknowledgement within scope", () => {
  assert.deepEqual(decodeDecisionChange(change, project, changeId), change);
  for (const changed of [{ project_id: work }, { id: work }, { acknowledged_scope_version: 3 },
    { scope_version: -1 }, { assessment_count: "2" }, { parent_change_id: "bad" },
    { review_work_project_id: "bad" }, { review_closed: "false" }, { successor_change_id: "bad" }, { extra: true }]) {
    assert.throws(() => decodeDecisionChange({ ...change, ...changed }, project, changeId));
  }
  assert.deepEqual(decodeDecisionRevisionChange({ change_id: changeId, kind: "record-existing",
    reason: "Records the same ruling", governing_reference: "ADR-00001-1", source_work_item_id: work }),
  { change_id: changeId, kind: "record-existing", reason: "Records the same ruling",
    governing_reference: "ADR-00001-1", source_work_item_id: work });
  assert.throws(() => decodeDecisionRevisionChange({ change_id: changeId, kind: "record-existing",
    reason: null, governing_reference: null, source_work_item_id: work }));
});

test("adoption, acknowledgement and human admission bind distinct result identities", () => {
  assert.equal(matchDecisionChangeMutation(change, project, { action: "adopt", work_item_id: work,
    expected_work_version: 1, assessment_ids: [assessment] }).id, changeId);
  assert.throws(() => matchDecisionChangeMutation(change, project, { action: "adopt", work_item_id: project,
    expected_work_version: 1, assessment_ids: [assessment] }));
  assert.throws(() => matchDecisionChangeMutation({ ...change, acknowledged_scope_version: 1 }, project, payload));
  assert.throws(() => matchDecisionChangeMutation(change, project, { ...payload, expected_scope_version: 1 }));
  assert.equal(matchDecisionChangeMutation({ ...change, id: work, parent_change_id: changeId }, project,
    { action: "admit", change_id: changeId, expected_scope_version: 2 }).id, work);
  assert.throws(() => matchDecisionChangeMutation(change, project,
    { action: "admit", change_id: changeId, expected_scope_version: 2 }));
});

test("moved drivers, driverless held answers and admitted successors retain their source identity", () => {
  const moved = { ...change, review_work_project_id: work, review_closed: true, successor_change_id: assessment };
  assert.deepEqual(decodeDecisionChange(moved, project), moved);
  const held = { ...change, review_work_item_id: null, review_work_project_id: null,
    scope_version: 0, acknowledged_scope_version: 0, assessment_count: 0, revision_count: 1, pending_revision_count: 1 };
  assert.deepEqual(decodeDecisionChange(held, project), held);
});

test("wrong scope success stays unresolved and retry uses identical frozen bytes", async () => {
  const sent = [];
  const registry = new MutationIntentRegistry(async (url, init) => {
    sent.push({ url, body: init.body });
    return Response.json(sent.length === 1 ? { ...change, acknowledged_scope_version: 1 } : change);
  }, () => operation);
  const intent = { kind: "decision_change_update", slot: "change-action", projectId: project,
    conflictKeys: [`work:${project}:${work}`], method: "POST", path: request.path, payload };
  await assert.rejects(registry.execute(intent), { state: "unresolved" });
  assert.equal(registry.blocks(intent.conflictKeys), true);
  assert.deepEqual(await registry.retry(intent.slot), change);
  assert.deepEqual(sent[0], sent[1]);
  assert.equal(registry.blocks(intent.conflictKeys), false);
});

test("scope conflicts and closed review stops are definitive without admitting new work", async () => {
  for (const code of ["decision_change_closed", "decision_change_scope_conflict",
    "decision_change_scope_unacknowledged", "decision_change_lineage_conflict", "decision_change_adoption_conflict"]) {
    const outcome = await classifyMutationResponse(request, Response.json({ detail: { code,
      message: "Read current review scope before a new intent", context: {} } }, { status: 409 }));
    assert.equal(outcome.type, "rejected", code);
  }
  const wrong = await classifyMutationResponse(request, Response.json({ ...change, project_id: work }));
  assert.equal(wrong.type, "unresolved");
});
