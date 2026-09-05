import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeHumanGateRevision,
  decodeMergeReviewRevision,
  sameHumanGateRevision,
  sameMergeReviewRevision,
  validHumanGateRevision,
  validMergeReviewRevision
} from "../lib/revision-codecs.ts";
import { invalidMutationBody } from "../lib/proxy-policy.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const checkpoint = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";
const other = "26a3a437-0af3-405a-ab82-7932d17869e0";
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";

const contracts = [
  {
    name: "human gate",
    count: "relationship_event_count",
    minimum: 0,
    valid: validHumanGateRevision,
    decode: decodeHumanGateRevision,
    same: sameHumanGateRevision,
    defaultError: "Mnemonic returned an invalid human-gate revision.",
    path: `projects/${project}/work-items/${work}/gates/${other}/resolve`,
    body: (revision) => ({
      resolution: "Proceed.",
      resolved_by_client: "dashboard",
      resolved_by_session_id: "tab",
      resolved_by_model: null,
      reviewed_context_revision: revision,
      client_operation_id: operation
    })
  },
  {
    name: "merge review",
    count: "work_event_count",
    minimum: 1,
    valid: validMergeReviewRevision,
    decode: decodeMergeReviewRevision,
    same: sameMergeReviewRevision,
    defaultError: "Mnemonic returned an invalid merge review revision.",
    path: `projects/${project}/work-items/${work}/merge`,
    body: (revision) => ({
      destination_work_item_id: other,
      reviewed_source_revision: revision,
      reviewed_destination_revision: revision,
      rationale: "Same objective.",
      merged_by_client: "dashboard",
      merged_by_session_id: "tab",
      merged_by_model: null,
      client_operation_id: operation
    })
  }
];

for (const contract of contracts) {
  const revision = {
    work_version: 1,
    context_checkpoint_id: checkpoint,
    [contract.count]: contract.minimum
  };

  test(`${contract.name} request and response acceptance agree at structural boundaries`, () => {
    for (const value of [
      revision,
      { ...revision, context_checkpoint_id: checkpoint.toUpperCase() },
      { ...revision, work_version: Number.MAX_SAFE_INTEGER },
      { ...revision, [contract.count]: Number.MAX_SAFE_INTEGER },
      Object.assign(Object.create(null), revision)
    ]) {
      assert.equal(contract.valid(value), true);
      assert.equal(contract.decode(value), value);
      assert.equal(invalidMutationBody(contract.path, "POST", contract.body(value)), null);
    }

    const withoutEachField = Object.keys(revision).map((field) => {
      const value = { ...revision };
      delete value[field];
      return value;
    });
    for (const value of [
      null,
      undefined,
      [],
      ...withoutEachField,
      { ...revision, extra: true },
      { ...revision, context_checkpoint_id: null },
      { ...revision, context_checkpoint_id: "not-a-uuid" },
      ...[0, -1, 1.5, "1", Infinity, NaN, Number.MAX_SAFE_INTEGER + 1].map(
        (work_version) => ({ ...revision, work_version })
      ),
      ...[contract.minimum - 1, 1.5, "1", Infinity, NaN, Number.MAX_SAFE_INTEGER + 1].map(
        (count) => ({ ...revision, [contract.count]: count })
      )
    ]) {
      assert.equal(contract.valid(value), false);
      assert.throws(() => contract.decode(value), { message: contract.defaultError });
      assert.throws(
        () => contract.decode(value, "Caller-specific revision error."),
        { message: "Caller-specific revision error." }
      );
      assert.notEqual(invalidMutationBody(contract.path, "POST", contract.body(value)), null);
    }
  });

  test(`${contract.name} equality preserves UUID case and compares every revision field`, () => {
    assert.equal(
      contract.same(revision, { ...revision, context_checkpoint_id: checkpoint.toUpperCase() }),
      true
    );
    for (const changed of [
      { work_version: 2 },
      { context_checkpoint_id: other },
      { [contract.count]: contract.minimum + 1 }
    ]) {
      assert.equal(contract.same(revision, { ...revision, ...changed }), false);
    }
  });
}
