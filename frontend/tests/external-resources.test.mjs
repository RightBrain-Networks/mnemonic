import assert from "node:assert/strict";
import test from "node:test";
import { decodeResources, MAX_RESOURCE_CAPACITY, parseResourceDraft } from "../lib/external-resources.ts";

const projectId = "11111111-1111-4111-8111-111111111111";
const resourceId = "22222222-2222-4222-8222-222222222222";
const leaseId = "33333333-3333-4333-8333-333333333333";
const draft = { name: "  pytest workers  ", capacity: "16", minimum_minutes: "10", default_minutes: "15", maximum_minutes: "120" };
const resource = { ...parseResourceDraft(draft), id: resourceId, project_id: projectId, available: 8,
  leases: [{ id: leaseId, resource_id: resourceId, holder_client: "codex", holder_session_id: "pytest",
    quantity: 8, acquired_at: "2026-10-01T10:00:00Z", expires_at: "2026-10-01T10:15:00Z" }] };

test("external resource forms support unsigned 32-bit capacity and ordered lease durations", () => {
  assert.equal(parseResourceDraft(draft).name, "pytest workers");
  assert.equal(parseResourceDraft({ ...draft, capacity: String(MAX_RESOURCE_CAPACITY) }).capacity, MAX_RESOURCE_CAPACITY);
  for (const invalid of [{ capacity: "4294967296" }, { capacity: "0" }, { capacity: "1.5" },
    { maximum_minutes: "14" }, { minimum_minutes: "16" }, { name: " " }]) {
    assert.equal(parseResourceDraft({ ...draft, ...invalid }), null);
  }
});

test("external resource reads keep every lease scoped and reconcile available units", () => {
  const decode = (items) => decodeResources({ project_id: projectId, items }, projectId);
  assert.deepEqual(decode([resource]), [resource]);
  assert.throws(() => decode([resource, resource]));
  assert.throws(() => decode([{ ...resource, project_id: leaseId }]));
  assert.throws(() => decode([{ ...resource, available: 0 }]));
  assert.throws(() => decode([{ ...resource, leases: [{ ...resource.leases[0], resource_id: leaseId }] }]));
});
