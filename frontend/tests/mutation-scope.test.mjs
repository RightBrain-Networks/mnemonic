import assert from "node:assert/strict";
import test from "node:test";
import {
  MutationIntentError,
  MutationIntentRegistry,
  isDispatchedMutation,
  matchesMutationScope,
  mutationCreateKey,
  mutationGateKey,
  mutationProjectKey,
  mutationWorkKey,
  selectMutationScope
} from "../lib/mutation-intent.ts";
import { selectMutationRecovery } from "../lib/mutation-recovery.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const otherProject = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const destination = "f1cf3691-7d28-4716-94a9-4867b341a685";
const otherWork = "26a3a437-0af3-405a-ab82-7932d17869e0";
const gate = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const checkpoint = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const actor = { actor_client: "dashboard", actor_session_id: "scope-test" };
const revision = { work_version: 1, context_checkpoint_id: checkpoint };

function input(kind, workItemId = work, projectId = project, target = destination) {
  const base = {
    kind,
    slot: `${kind.replaceAll("_", "-")}:${projectId}:${workItemId}`,
    projectId,
    conflictKeys: [mutationWorkKey(projectId, workItemId)],
    method: "POST",
    path: `/projects/${projectId}/work-items/${workItemId}`,
    payload: { expected_version: 1, actor }
  };
  if (kind === "create_work") return {
    ...base,
    slot: `create-work:${projectId}`,
    conflictKeys: [mutationCreateKey(projectId)],
    path: `/projects/${projectId}/work-items`,
    payload: {
      title: "New work",
      summary: "Scope test.",
      status: "pending",
      priority: 0,
      initial_checkpoint: {
        prompt: "Initial context.",
        source_client: "dashboard",
        source_session_id: "scope-test"
      }
    }
  };
  if (kind === "merge_work") return {
    ...base,
    conflictKeys: [
      mutationWorkKey(projectId, workItemId),
      mutationWorkKey(projectId, target)
    ],
    path: `${base.path}/merge`,
    payload: {
      destination_work_item_id: target,
      reviewed_source_revision: { ...revision, work_event_count: 1 },
      reviewed_destination_revision: { ...revision, work_event_count: 1 },
      rationale: "Same objective.",
      merged_by_client: "dashboard",
      merged_by_session_id: "scope-test",
      merged_by_model: null
    }
  };
  if (kind === "resolve_human_input") return {
    ...base,
    conflictKeys: [
      mutationWorkKey(projectId, workItemId),
      mutationGateKey(projectId, gate)
    ],
    path: `${base.path}/gates/${gate}/resolve`,
    payload: {
      resolution: "Proceed.",
      resolved_by_client: "dashboard",
      resolved_by_session_id: "scope-test",
      resolved_by_model: null,
      reviewed_context_revision: { ...revision, relationship_event_count: 0 }
    }
  };
  if (kind === "update_work") return {
    ...base,
    method: "PATCH",
    payload: { ...base.payload, title: "Updated work" }
  };
  assert.equal(kind, "delete_work");
  return { ...base, path: `${base.path}/delete` };
}

function stagedRegistry() {
  const replies = [];
  let serial = 0;
  const registry = new MutationIntentRegistry(
    () => new Promise((resolve) => replies.push(resolve)),
    () => `91b9168a-37d1-4a6a-aa1f-${String(++serial).padStart(12, "0")}`
  );
  return {
    registry,
    dispatch(slot) {
      // Observe rejection immediately so a deliberately unresolved request never leaks a rejection.
      return registry.retry(slot).then(
        () => assert.fail("The staged request must remain unresolved."),
        (error) => error
      );
    },
    async finish(pending, state) {
      const resolve = replies.shift();
      assert.ok(resolve, "The registry must have sent the staged request.");
      resolve(new Response(JSON.stringify({
        detail: state === "safety_conflict"
          ? { code: "client_operation_conflict", message: "Operation mismatch.", context: {} }
          : "Upstream unavailable."
      }), { status: state === "safety_conflict" ? 409 : 502 }));
      const error = await pending;
      assert.ok(error instanceof MutationIntentError);
      assert.equal(error.state, state);
    }
  };
}

function assertBlockingParity(registry, spec, expectedState) {
  const snapshot = registry.getSnapshot();
  const summary = snapshot.find((intent) => intent.slot === spec.slot);
  const dispatched = expectedState !== "prepared";
  assert.equal(summary.state, expectedState);
  assert.equal(isDispatchedMutation(summary), dispatched);
  assert.equal(matchesMutationScope(summary, { projectId: project.toUpperCase() }), true);
  assert.equal(registry.hasDispatched(), dispatched);
  assert.equal(selectMutationScope(snapshot).blocked, registry.hasDispatched());
  assert.equal(selectMutationScope(snapshot).intents.length, dispatched ? 1 : 0);

  for (const [projectId, expected] of [
    [project, dispatched],
    [project.toUpperCase(), dispatched],
    [otherProject, false]
  ]) {
    const selected = selectMutationScope(snapshot, { projectId });
    assert.equal(selected.blocked, expected);
    assert.equal(selected.blocked, registry.hasDispatchedForProject(projectId));
  }

  for (const keys of [
    ...spec.conflictKeys.map((key) => [key]),
    [mutationWorkKey(project.toUpperCase(), work.toUpperCase())],
    [mutationWorkKey(otherProject, work)],
    [mutationWorkKey(project, otherWork)],
    [mutationProjectKey(project)],
    [mutationCreateKey(project)],
    []
  ]) {
    const expected = dispatched && keys.some((key) => spec.conflictKeys.includes(key));
    const selected = selectMutationScope(snapshot, { conflictKeys: keys });
    assert.equal(selected.blocked, expected);
    assert.equal(selected.blocked, registry.blocks(keys));
    assert.deepEqual(
      selected.intents.map((intent) => intent.slot),
      expected ? [spec.slot] : []
    );
    assert.equal(
      selectMutationScope(snapshot, { conflictKeys: keys, exceptSlot: spec.slot }).blocked,
      registry.blocks(keys, spec.slot)
    );
    assert.equal(registry.blocks(keys, spec.slot), false);
  }

  const challenger = { ...spec, slot: `challenger:${spec.slot}` };
  if (dispatched) {
    assert.throws(
      () => registry.prepare(challenger),
      (error) => error instanceof MutationIntentError && error.state === "blocked"
    );
  } else {
    registry.prepare(challenger);
    assert.equal(registry.discardPrepared(challenger.slot), true);
  }
}

for (const kind of ["delete_work", "merge_work", "resolve_human_input", "create_work"]) {
  test(`${kind} UI scopes agree with registry blocking through all four intent states`, async () => {
    const staged = stagedRegistry();
    const spec = input(kind);
    staged.registry.prepare(spec);
    assertBlockingParity(staged.registry, spec, "prepared");

    const initial = staged.dispatch(spec.slot);
    assertBlockingParity(staged.registry, spec, "in_flight");
    await staged.finish(initial, "unresolved");
    assertBlockingParity(staged.registry, spec, "unresolved");

    const retry = staged.dispatch(spec.slot);
    assertBlockingParity(staged.registry, spec, "in_flight");
    await staged.finish(retry, "safety_conflict");
    assertBlockingParity(staged.registry, spec, "safety_conflict");
  });
}

test("scope matching combines project, conflict keys, kinds, exact slot and own-slot exclusion", () => {
  const staged = stagedRegistry();
  const spec = input("merge_work");
  staged.registry.prepare(spec);
  const [summary] = staged.registry.getSnapshot();
  const scope = {
    projectId: project.toUpperCase(),
    conflictKeys: [mutationWorkKey(project, otherWork), mutationWorkKey(project, destination)],
    kinds: ["delete_work", "merge_work"],
    slot: spec.slot,
    exceptSlot: "another-slot"
  };
  assert.equal(matchesMutationScope(summary, scope), true);
  assert.equal(selectMutationScope([summary], scope).blocked, false, "Prepared is never blocking.");
  assert.equal(isDispatchedMutation(undefined), false);
  for (const changed of [
    { projectId: otherProject },
    { conflictKeys: [mutationWorkKey(otherProject, destination)] },
    { conflictKeys: [] },
    { kinds: ["delete_work"] },
    { kinds: [] },
    { slot: input("merge_work", otherWork).slot },
    { slot: spec.slot.toUpperCase() },
    { exceptSlot: spec.slot }
  ]) {
    assert.equal(matchesMutationScope(summary, { ...scope, ...changed }), false);
  }
});

// Use immutable summaries emitted by real registries without retaining any pending timers.
async function summaryInState(spec, state) {
  const staged = stagedRegistry();
  staged.registry.prepare(spec);
  if (state === "prepared") return staged.registry.getSnapshot()[0];
  const pending = staged.dispatch(spec.slot);
  if (state === "in_flight") {
    const summary = staged.registry.getSnapshot()[0];
    await staged.finish(pending, "unresolved");
    return summary;
  }
  await staged.finish(pending, state);
  return staged.registry.getSnapshot()[0];
}

function slots(intents) {
  return intents.map((intent) => intent.slot);
}

test("recovery assigns every dispatched intent exactly once with modal priority and exact merge ownership", async () => {
  const specs = [
    [input("create_work"), "unresolved"],
    [input("delete_work"), "safety_conflict"],
    [input("merge_work"), "in_flight"],
    [input("merge_work", otherWork, project, work), "unresolved"],
    [input("resolve_human_input"), "unresolved"],
    [input("update_work"), "in_flight"],
    [input("delete_work", work, otherProject), "unresolved"],
    [input("create_work", work, otherProject), "unresolved"],
    [input("update_work", destination), "prepared"]
  ];
  const intents = await Promise.all(specs.map(([spec, state]) => summaryInState(spec, state)));
  const targets = {
    createWorkKey: mutationCreateKey(project),
    deleteWorkKey: mutationWorkKey(project, work),
    mergeSlot: input("merge_work").slot,
    openedWorkKey: mutationWorkKey(project, work)
  };
  const owned = selectMutationRecovery(intents, targets);
  assert.deepEqual(slots(owned.createDialog), [specs[0][0].slot]);
  assert.deepEqual(slots(owned.deleteDialog), [specs[1][0].slot]);
  assert.deepEqual(slots(owned.mergePanel), [specs[2][0].slot]);
  assert.deepEqual(slots(owned.openedPane), [
    specs[3][0].slot, specs[4][0].slot, specs[5][0].slot
  ]);
  assert.deepEqual(slots(owned.global), [specs[6][0].slot, specs[7][0].slot]);

  const dispatched = intents.filter(isDispatchedMutation);
  const assigned = Object.values(owned).flat();
  assert.equal(new Set(assigned.map((intent) => intent.slot)).size, dispatched.length);
  assert.deepEqual(slots(assigned).sort(), slots(dispatched).sort());
  assert.ok(assigned.every((intent) => intents.includes(intent)), "Ownership preserves each summary.");
  assert.equal(assigned.some((intent) => intent.state === "prepared"), false);

  const noModals = selectMutationRecovery(intents, { openedWorkKey: targets.openedWorkKey });
  assert.deepEqual(slots(noModals.openedPane), specs.slice(1, 6).map(([spec]) => spec.slot));
  assert.deepEqual(slots(noModals.global), [
    specs[0][0].slot, specs[6][0].slot, specs[7][0].slot
  ]);
  assert.deepEqual(noModals.createDialog, []);
  assert.deepEqual(noModals.deleteDialog, []);
  assert.deepEqual(noModals.mergePanel, []);

  const closed = selectMutationRecovery(intents.values(), {});
  assert.deepEqual(slots(closed.global), slots(dispatched));
  assert.deepEqual(closed.openedPane, []);
});

test("a merge targeting opened work stays reachable outside a different source's merge panel", async () => {
  const spec = input("merge_work", otherWork, project, work);
  const intent = await summaryInState(spec, "unresolved");
  const openedWorkKey = mutationWorkKey(project, work);
  const otherSource = selectMutationRecovery([intent], {
    mergeSlot: input("merge_work").slot,
    openedWorkKey
  });
  assert.deepEqual(otherSource.mergePanel, []);
  assert.deepEqual(otherSource.openedPane, [intent]);

  const sourcePanel = selectMutationRecovery([intent], { mergeSlot: spec.slot, openedWorkKey });
  assert.deepEqual(sourcePanel.mergePanel, [intent]);
  assert.deepEqual(sourcePanel.openedPane, []);

  const closed = selectMutationRecovery([intent], {
    mergeSlot: input("merge_work").slot,
    openedWorkKey: mutationWorkKey(otherProject, work)
  });
  assert.deepEqual(closed.global, [intent]);
});
