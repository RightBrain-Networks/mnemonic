import assert from "node:assert/strict";
import test from "node:test";
import { decodeAssessmentDetail, decodeImpact, decodeDisposition } from "../lib/knowledge-impact.ts";
import { decodeScope } from "../lib/knowledge.ts";
const project = "11111111-1111-4111-8111-111111111111", id = "22222222-2222-4222-8222-222222222222";
const coverage = { traversal_complete: false, provenance_complete: false, max_depth: 8, candidate_limit: 1000, visited_revisions: 3, reasons: ["Depth limit reached"], related_semantics: "unknown", path_policy: "one deterministic shortest path per revision" };
const candidate = { decision_id: "ADR-00002", reference: "ADR-00002-0", revision: 0, title: "Dependent decision", state: "accepted", is_current: true, relationship: "depends-on", path: ["ADR-00001-0", "ADR-00002-0"], rationale: "Uses the original transport protocol", scopes: [{ id, revision: 0, commit_hash: "a".repeat(40), repository: null, completion_report_id: null, merged_commit_hashes: [], work_item_id: null, agent_session_id: "native-session" }], related_work_item_ids: [], related_artifact_ids: [], evidence_truncated: false };
const disposition = { id, project_id: project, assessment_id: id, version: 1, candidate_reference: candidate.reference, disposition: "change-required", reason: "Replace the old transport adapter", remediation_work_item_id: id, actor: { actor_client: "dashboard", actor_session_id: "browser-session", actor_model: null }, created_at: "2026-10-22T12:00:00Z" };
const detail = { id, project_id: project, trigger: "revision", before_reference: "ADR-00001-0", after_reference: "ADR-00001-1", extent: null, reason: null, diff: { changed_fields: ["sha256"], before_sha256: "a".repeat(64), after_sha256: "b".repeat(64), unified_diff: "-old\n+new", truncated: false }, candidate_count: 1, coverage, created_at: disposition.created_at, version: 1, candidates: [candidate], dispositions: [disposition], disposition_history_total: 1, limit: 25, offset: 0, next_offset: null };

test("impact preserves pinned paths, unknown repository coverage and traversal limits", () => {
  const page = { project_id: project, origin_reference: "ADR-00001-0", items: [candidate], total: 1, limit: 25, offset: 0, next_offset: null, coverage };
  assert.deepEqual(decodeImpact(page, project, "ADR-00001-0"), page);
  for (const change of [{ coverage: { ...coverage, provenance_complete: true } }, { origin_reference: "ADR-00001-1" }, { items: [{ ...candidate, path: [candidate.reference] }] }, { items: [{ ...candidate, scopes: [{ ...candidate.scopes[0], revision: 1 }] }] }]) assert.throws(() => decodeImpact({ ...page, ...change }, project, "ADR-00001-0"));
  const related = { ...candidate, relationship: "related-only", rationale: null };
  assert.equal(decodeImpact({ ...page, items: [related] }, project).items[0].relationship, "related-only");
});
test("assessment details enforce bounded candidate pages and latest disposition identity", () => {
  assert.deepEqual(decodeAssessmentDetail(detail, project, id), detail);
  for (const change of [{ dispositions: [{ ...disposition, candidate_reference: "ADR-00003-0" }] }, { dispositions: [disposition, disposition] }, { version: 0 }, { candidate_count: 0 }, { id: project }]) assert.throws(() => decodeAssessmentDetail({ ...detail, ...change }, project, id));
  assert.equal(decodeAssessmentDetail({ ...detail, candidates: [], dispositions: [], offset: 25 }, project, id).offset, 25);
});
test("append-only disposition evidence requires actor, nonblank reason and a valid remediation UUID", () => {
  assert.deepEqual(decodeDisposition(disposition, project, id), disposition);
  for (const change of [{ actor: null }, { reason: " " }, { remediation_work_item_id: "not-work" }, { candidate_reference: "ADR-00002" }, { disposition: "approved" }, { version: 0 }]) assert.throws(() => decodeDisposition({ ...disposition, ...change }, project, id));
});
test("commit lookup decodes repository identity and reconciled full hashes without inventing missing identity", () => {
  const scope = { ...candidate.scopes[0], project_id: project, decision_id: candidate.decision_id, created_at: disposition.created_at };
  assert.equal(decodeScope(scope, project).repository, null);
  assert.deepEqual(decodeScope({ ...scope, repository: "github.com/team/project", merged_commit_hashes: ["b".repeat(40)] }, project).merged_commit_hashes, ["b".repeat(40)]);
  assert.throws(() => decodeScope({ ...scope, merged_commit_hashes: ["abcdef0"] }, project));
});

test("assessment status cannot overstate resolution or hide pending candidates", () => {
  const pending = { ...detail, unassessed_count: 1, change_required_count: 0, status: "pending" };
  assert.equal(decodeAssessmentDetail(pending, project, id).status, "pending");
  assert.throws(() => decodeAssessmentDetail({ ...pending, status: "resolved" }, project, id));
  assert.throws(() => decodeAssessmentDetail({ ...pending, change_required_count: 1, status: "change-required" }, project, id));
});
