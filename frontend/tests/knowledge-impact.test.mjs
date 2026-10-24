import assert from "node:assert/strict";
import test from "node:test";
import { decodeAssessmentDetail, decodeImpact, decodeDisposition, decodeImpactCandidate } from "../lib/knowledge-impact.ts";
import { decodeScope } from "../lib/knowledge.ts";
const project = "11111111-1111-4111-8111-111111111111", id = "22222222-2222-4222-8222-222222222222";
const author = (reference) => ({ reference, author_kind: "agent", agent_session_id: "native-session", model_id: "test-model", authored_at: "2026-10-22T11:00:00Z", work_observed_at: "2026-10-22T12:00:00Z", active_work_items: [], active_work_items_truncated: false });
const coverage = { traversal_complete: false, provenance_complete: false, max_depth: 8, candidate_limit: 1000, visited_revisions: 3, reasons: ["Depth limit reached"], related_semantics: "unknown", path_policy: "one deterministic shortest path per revision" };
const candidate = { decision_id: "ADR-00002", reference: "ADR-00002-0", revision: 0, title: "Dependent decision", state: "accepted", is_current: true, relationship: "depends-on", path: ["ADR-00001-0", "ADR-00002-0"], rationale: "Uses the original transport protocol", scopes: [{ id, revision: 0, commit_hash: "a".repeat(40), repository: null, completion_report_id: null, merged_commit_hashes: [], work_item_id: null, agent_session_id: "native-session" }], related_work_item_ids: [], related_artifact_ids: [], evidence_truncated: false };
const disposition = { id, project_id: project, assessment_id: id, version: 1, candidate_reference: candidate.reference, disposition: "change-required", reason: "Replace the old transport adapter", remediation_work_item_id: id, actor: { actor_client: "dashboard", actor_session_id: "browser-session", actor_model: null }, created_at: "2026-10-22T12:00:00Z" };
const detail = { kind: "code-impact", total: 1, context_count: 0, before_author: author("ADR-00001-0"), after_author: author("ADR-00001-1"), id, project_id: project, trigger: "revision", before_reference: "ADR-00001-0", after_reference: "ADR-00001-1", extent: null, reason: null, diff: { changed_fields: ["sha256"], before_sha256: "a".repeat(64), after_sha256: "b".repeat(64), unified_diff: "-old\n+new", truncated: false }, candidate_count: 1, coverage, created_at: disposition.created_at, version: 1, candidates: [candidate], dispositions: [disposition], disposition_history_total: 1, limit: 25, offset: 0, next_offset: null };

test("optional citation excerpts preserve source hashes and Unicode character limits", () => {
  const evidence = { source_sha256: "a".repeat(64), excerpts: ["🙂".repeat(400)], truncated: true };
  assert.deepEqual(decodeImpactCandidate({ ...candidate, citation_excerpts: evidence }).citation_excerpts, evidence);
  for (const citation_excerpts of [null, { ...evidence, source_sha256: "a" }, { ...evidence, excerpts: ["🙂".repeat(401)] }, { ...evidence, excerpts: ["x", "x", "x", "x"] }, { ...evidence, truncated: "false" }]) {
    assert.throws(() => decodeImpactCandidate({ ...candidate, citation_excerpts }));
  }
});

test("impact preserves pinned paths, unknown repository coverage and traversal limits", () => {
  const page = { kind: "code-impact", candidate_count: 1, context_count: 0, origin_author: author("ADR-00001-0"), project_id: project, origin_reference: "ADR-00001-0", items: [candidate], total: 1, limit: 25, offset: 0, next_offset: null, coverage };
  assert.deepEqual(decodeImpact(page, project, "ADR-00001-0"), page);
  for (const change of [{ coverage: { ...coverage, provenance_complete: true } }, { origin_reference: "ADR-00001-1" }, { items: [{ ...candidate, path: [candidate.reference] }] }, { items: [{ ...candidate, scopes: [{ ...candidate.scopes[0], revision: 1 }] }] }]) assert.throws(() => decodeImpact({ ...page, ...change }, project, "ADR-00001-0"));
  const related = { ...candidate, relationship: "related-only", rationale: null };
  assert.equal(decodeImpact({ ...page, kind: "context", candidate_count: 0, context_count: 1, items: [related] }, project, undefined, "context").items[0].relationship, "related-only");
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

test("context is paged independently and cannot masquerade as required code impact", () => {
  const context = { ...candidate, relationship: "depends-on-prerequisite" };
  const page = { ...detail, kind: "context", context_count: 1, candidates: [context], dispositions: [], total: 1 };
  assert.equal(decodeAssessmentDetail(page, project, id, "context").context_count, 1);
  assert.throws(() => decodeAssessmentDetail(page, project, id));
  assert.throws(() => decodeAssessmentDetail({ ...page, candidates: [candidate] }, project, id, "context"));
  assert.throws(() => decodeAssessmentDetail({ ...page, total: 2 }, project, id, "context"));
  const finding = { ...page, unassessed_count: 1, change_required_count: 1, status: "change-required" };
  assert.equal(decodeAssessmentDetail(finding, project, id, "context").status, "change-required");
});

test("author holdings must match the pinned revision and be active at observation", () => {
  const held = { work_item_id: id, title: "Build the rule", holder_client: "codex", purpose: "implementation", expires_at: "2026-10-22T12:15:00Z" };
  const updated = { ...detail, after_author: { ...author("ADR-00001-1"), active_work_items: [held] } };
  assert.deepEqual(decodeAssessmentDetail(updated, project, id).after_author.active_work_items, [held]);
  for (const change of [
    { reference: "ADR-00001-0" }, { active_work_items: [held, held] },
    { active_work_items: [{ ...held, expires_at: "2026-10-22T12:00:00Z" }] },
    { author_kind: "human" }, { work_observed_at: "bad date" },
  ]) assert.throws(() => decodeAssessmentDetail({ ...updated, after_author: { ...updated.after_author, ...change } }, project, id));
});

test("missing-code warnings retain their exact revision identity", () => {
  const warning = { code: "origin_has_no_linked_commits", reference: "ADR-00001-0", message: "ADR-00001-0 has no linked commits." };
  const page = { ...detail, coverage: { ...coverage, warnings: [warning] } };
  assert.deepEqual(decodeAssessmentDetail(page, project, id).coverage.warnings, [warning]);
  assert.throws(() => decodeAssessmentDetail({ ...page, coverage: { ...coverage, warnings: [{ ...warning, reference: "ADR-00001" }] } }, project, id));
  assert.throws(() => decodeAssessmentDetail({ ...page, coverage: { ...coverage, warnings: [{ ...warning, reference: "ADR-00001-9" }] } }, project, id));
});


test("incoming related links require dispositions without asserting causal dependence", () => {
  const incoming = { ...candidate, relationship: "related-only", related_direction: "incoming", rationale: null };
  const assessment = { ...detail, candidates: [incoming] };
  assert.equal(decodeAssessmentDetail(assessment, project, id).candidates[0].relationship, "related-only");
  const page = { kind: "code-impact", candidate_count: 1, context_count: 0, origin_author: author("ADR-00001-0"), project_id: project, origin_reference: "ADR-00001-0", items: [incoming], total: 1, limit: 25, offset: 0, next_offset: null, coverage };
  assert.equal(decodeImpact(page, project).items[0].related_direction, "incoming");
  assert.throws(() => decodeImpact({ ...page, kind: "context", candidate_count: 0, context_count: 1 }, project, undefined, "context"));
  for (const related_direction of [undefined, null, "outgoing"]) {
    const contextual = { ...incoming, related_direction };
    assert.throws(() => decodeAssessmentDetail({ ...assessment, candidates: [contextual] }, project, id));
    assert.equal(decodeAssessmentDetail({ ...assessment, kind: "context", candidate_count: 0, context_count: 1, candidates: [contextual] }, project, id, "context").context_count, 1);
  }
  for (const malformed of [{ ...incoming, related_direction: "invalid" }, { ...incoming, related_direction: ["incoming"] }, { ...candidate, related_direction: "incoming" }]) {
    assert.throws(() => decodeAssessmentDetail({ ...assessment, candidates: [malformed] }, project, id));
  }
});
