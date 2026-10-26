import assert from "node:assert/strict";
import test from "node:test";
import { decodeDecisionChange } from "../lib/decision-changes.ts";
import { decodeLineageWorkPage } from "../lib/decision-lineage.ts";
import { decisionChangeOptions } from "../lib/knowledge-queries.ts";

const project = "10000000-0000-4000-8000-000000000001";
const otherProject = "10000000-0000-4000-8000-000000000002";
const work = "20000000-0000-4000-8000-000000000001";
const driver = "20000000-0000-4000-8000-000000000002";
const review = "30000000-0000-4000-8000-000000000001";
const now = "2026-10-26T00:00:00Z";
const readiness = { lifecycle_status: "pending", is_duplicate: false, canonical_work_item_id: work,
  is_terminal: false, has_active_lease: false, has_dropped_lease: false, active_lease: null,
  unresolved_blocker_count: 0, is_blocked: false, unresolved_gate_count: 0, is_gated: false,
  is_ready: true, display_state: "pending" };
const member = { work_item_id: work, project_id: otherProject, title: "Fix the known defect", status: "pending",
  deleted_at: null, canonical_work_item_id: work, readiness, is_decision_source: false,
  discovered_from: { items: [{ work_item_id: driver, project_id: project }], total: 1, omitted_count: 0 },
  code_review: null, review_follow_up: null,
  remediation_origin: { review_id: review, source_work_item_id: driver, source_project_id: project, depth: 1 } };
const page = { items: [member], total: 2, offset: 0, limit: 1, next_offset: 1 };

test("lineage preserves cross-project remediation and explicit page coverage", () => {
  assert.deepEqual(decodeLineageWorkPage(page, driver), page);
  const lease = { holder_client: "codex", holder_session_id: "native", acquired_at: now,
    renewed_at: now, expires_at: "2026-10-26T00:15:00Z" };
  const active = { ...member, readiness: { ...readiness, active_lease: lease,
    has_active_lease: true, is_ready: false, display_state: "active" } };
  assert.equal(decodeLineageWorkPage({ ...page, items: [active] }, driver).items[0].readiness.active_lease, lease);
  const change = { id: review, project_id: project, origin_reference: null, review_work_item_id: driver,
    review_work_project_id: project, parent_change_id: null, review_closed: false, successor_change_id: null,
    scope_version: 0, acknowledged_scope_version: 0, assessment_count: 0, revision_count: 0,
    pending_revision_count: 0, created_at: now, updated_at: now, lineage_work: page };
  assert.deepEqual(decodeDecisionChange(change, project, review).lineage_work, page);
});
test("deleted members remain unavailable and malformed lineage cannot hide coverage", () => {
  const deleted = { ...member, deleted_at: now, readiness: null };
  assert.equal(decodeLineageWorkPage({ ...page, items: [deleted] }, driver).items[0].readiness, null);
  for (const item of [{ ...deleted, readiness }, { ...member, readiness: null },
    { ...member, work_item_id: driver }, { ...member, canonical_work_item_id: driver },
    { ...member, project_id: "wrong" }, { ...member, discovered_from: { ...member.discovered_from, total: 2 } },
    { ...member, remediation_origin: { ...member.remediation_origin, depth: 3 } },
    { ...member, code_review: { review_id: review, state: "invented", status: null } }]) {
    assert.throws(() => decodeLineageWorkPage({ ...page, items: [item] }, driver));
  }
  assert.throws(() => decodeLineageWorkPage({ ...page, items: [member, member], limit: 2, next_offset: null }, driver));
  for (const broken of [{ ...page, items: [], total: 50, limit: 20, next_offset: null },
    { ...page, next_offset: null }, { ...page, next_offset: 2 }, { ...page, extra: true }]) {
    assert.throws(() => decodeLineageWorkPage(broken, driver));
  }
});
test("lineage pagination has separate cache identity and forwards the exact offset", async (t) => {
  const urls = [];
  t.mock.method(globalThis, "fetch", async url => { urls.push(String(url)); return Response.json({}); });
  const first = decisionChangeOptions(project, review), later = decisionChangeOptions(project, review, 20);
  assert.notDeepEqual(first.queryKey, later.queryKey);
  await assert.rejects(later.queryFn({ signal: new AbortController().signal }));
  assert.match(urls[0], /lineage_limit=20&lineage_offset=20$/);
});
