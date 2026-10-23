import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { decodeDecision, decodeDecisionContent, decodeDomain, decodeKnowledgePage, decodeScope, knowledgeLibraryPath, knowledgeNextOffset, normalizeDomainPrefix, selectedDecision, verifyDecisionContent } from "../lib/knowledge.ts";

const project = "1b7f6231-9bb8-45a5-b98e-865cd2b5fd70";
const domainId = "2b7f6231-9bb8-45a5-b98e-865cd2b5fd70";
const id = "3b7f6231-9bb8-45a5-b98e-865cd2b5fd70";
const timestamp = "2026-10-21T12:00:00Z";
const content = "# A decision\n\nPreserve café ☕.\n";
const domain = { id: domainId, project_id: project, name: "Architecture", prefix: "ADR", version: 1, created_at: timestamp, updated_at: timestamp, decision_count: 1 };
const decision = { id, project_id: project, domain_id: domainId, domain_name: "Architecture", prefix: "ADR", ordinal: 194, decision_id: "ADR-00194", reference: "ADR-00194-0", revision: 0, title: "A decision", state: "accepted", media_type: "text/markdown", size_bytes: Buffer.byteLength(content), sha256: createHash("sha256").update(content).digest("hex"), created_at: timestamp, updated_at: timestamp, author_kind: "agent", agent_session_id: "native-thread", model_id: "codex", related_decision_ids: ["ODR-00002"], related_work_item_ids: [], related_artifact_ids: [], supersedes: [], superseded_by: ["ADR-00195-1"], referenced_by: ["ODR-00002-0"], scope_count: 1 };

test("domain prefixes normalize to uppercase ASCII with one through five letters", () => {
  assert.equal(normalizeDomainPrefix(" adr "), "ADR");
  for (const invalid of ["", "ADR123", "SIXLET", "A-B", "é", "1"]) assert.throws(() => normalizeDomainPrefix(invalid));
  assert.deepEqual(decodeDomain(domain, project), domain);
  assert.throws(() => decodeDomain({ ...domain, project_id: domainId }, project));
  assert.throws(() => decodeDomain({ ...domain, prefix: "adr" }, project));
  assert.throws(() => decodeDomain({ ...domain, version: 0 }, project));
});

test("decision reads pin project, document and zero-based revision with inverse graph references", () => {
  assert.deepEqual(decodeDecision(decision, project, "ADR-00194"), decision);
  for (const mutation of [
    { project_id: domainId }, { domain_id: "bad" }, { decision_id: "ADR-00195" }, { revision: 1 },
    { state: "draft" }, { media_type: "text/html" }, { sha256: "bad" },
    { related_work_item_ids: ["../path"] }, { superseded_by: ["ADR-00195"] }, { referenced_by: ["ODR-00002"] }
  ]) assert.throws(() => decodeDecision({ ...decision, ...mutation }, project, "ADR-00194"));
});

test("retained content verifies UTF-8 byte length and SHA-256 before display", async () => {
  const full = { ...decision, content, total_lines: 3, line_start: 1, line_end: 3 };
  assert.equal(decodeDecisionContent(full, project, "ADR-00194", 0).content, content);
  assert.throws(() => decodeDecisionContent(full, project, "ADR-00194", 1));
  assert.deepEqual(await verifyDecisionContent(full), full);
  await assert.rejects(verifyDecisionContent({ ...full, content: `${content} changed` }), /retained revision/);
  await assert.rejects(verifyDecisionContent({ ...full, sha256: "a".repeat(64) }), /retained revision/);
});

test("scope references preserve revision zero and require complete Git object IDs", () => {
  const scope = { id, project_id: project, decision_id: "ADR-00194", revision: 0, commit_hash: "a".repeat(40), agent_session_id: null, work_item_id: null, created_at: timestamp };
  assert.deepEqual(decodeScope(scope, project, "ADR-00194"), scope);
  assert.equal(decodeScope({ ...scope, commit_hash: "b".repeat(64) }, project, "ADR-00194").revision, 0);
  for (const hash of ["a".repeat(7), "a".repeat(41), "not-a-commit"]) assert.throws(() => decodeScope({ ...scope, commit_hash: hash }, project, "ADR-00194"));
  assert.throws(() => decodeScope(scope, domainId, "ADR-00194"));
  assert.throws(() => decodeScope(scope, project, "ADR-00195"));
});

test("scope reads retain original attribution alongside bounded closeout citations", () => {
  const citation = { report_id: id, work_item_id: domainId, actor_client: "codex", actor_session_id: "closeout-session", actor_model: null, created_at: timestamp };
  const scope = { id, project_id: project, decision_id: "ADR-00194", revision: 0, commit_hash: "a".repeat(40), agent_session_id: "merge-session", work_item_id: null, created_at: timestamp, completion_citations: [citation], completion_citations_truncated: false };
  assert.deepEqual(decodeScope(scope, project), scope);
  for (const change of [{ report_id: "bad" }, { work_item_id: null }, { actor_session_id: 1 }, { created_at: "invalid" }]) {
    assert.throws(() => decodeScope({ ...scope, completion_citations: [{ ...citation, ...change }] }, project));
  }
  assert.throws(() => decodeScope({ ...scope, completion_citations: [citation, citation] }, project));
  assert.throws(() => decodeScope({ ...scope, completion_citations_truncated: "false" }, project));
});

test("decision links retain revision zero and resolve both stable and revision-qualified identifiers", () => {
  assert.equal(knowledgeLibraryPath(project, "ADR-00194", 0), `/knowledge?project=${project}&decision=ADR-00194&revision=0`);
  assert.deepEqual(selectedDecision("decision=ADR-00194&revision=0"), { id: "ADR-00194", revision: 0 });
  assert.deepEqual(selectedDecision("decision=ADR-00194-13"), { id: "ADR-00194", revision: 13 });
  assert.deepEqual(selectedDecision("decision=ADR-100000"), { id: "ADR-100000" });
  assert.equal(selectedDecision("decision=../../path"), null);
});

test("knowledge pagination validates bounds and follows explicit next offsets", () => {
  const page = decodeKnowledgePage({ items: [domain], total: 5, limit: 2, offset: 0, next_offset: 3 }, (value) => decodeDomain(value, project));
  assert.equal(knowledgeNextOffset(page), 3);
  assert.equal(knowledgeNextOffset({ ...page, next_offset: undefined }), 1);
  assert.equal(knowledgeNextOffset({ ...page, next_offset: null }), null);
  assert.throws(() => decodeKnowledgePage({ ...page, next_offset: 0 }, (value) => value));
  assert.throws(() => decodeKnowledgePage({ ...page, total: 0 }, (value) => value));
  const shrunk = decodeKnowledgePage({ items: [], total: 1, limit: 25, offset: 25 }, (value) => value);
  assert.equal(knowledgeNextOffset(shrunk), null);
  assert.equal(shrunk.offset, 25);
});

test("decision edges retain exact target revisions, rationale and supersession extent without guessing legacy semantics", () => {
  const expanded = { ...decision,
    depends_on: [{ decision_id: "ODR-00002", revision: 0, rationale: "The owner approved retaining all revisions." }],
    supersession_details: [{ decision_id: "ADR-00193", revision: 2, extent: "partial", reason: "Only the retention period changed." }] };
  assert.deepEqual(decodeDecision(expanded, project), expanded);
  assert.equal(decodeDecision(decision, project).supersession_details, undefined);
  for (const change of [{ depends_on: [{ ...expanded.depends_on[0], revision: -1 }] }, { depends_on: [{ ...expanded.depends_on[0], rationale: " " }] }, { supersession_details: [{ ...expanded.supersession_details[0], extent: "unknown" }] }]) assert.throws(() => decodeDecision({ ...expanded, ...change }, project));
});

test("product domains and gate-backed decisions retain validated dashboard backlinks", () => {
  const productDomain = { ...domain, prefix: "PDR", is_product_decision_domain: true };
  assert.equal(decodeDomain(productDomain, project).is_product_decision_domain, true);
  assert.throws(() => decodeDomain({ ...productDomain, prefix: "ADR" }, project));
  assert.throws(() => decodeDomain({ ...productDomain, is_product_decision_domain: "yes" }, project));
  const product = { ...decision, prefix: "PDR", decision_id: "PDR-00194", reference: "PDR-00194-0",
    related_work_item_ids: [id], human_gate: { gate_id: domainId, work_item_id: id, work_project_id: project, status: "unresolved", question_version: 1 } };
  assert.equal(decodeDecision(product, project).human_gate.work_item_id, id);
  for (const human_gate of [null, { ...product.human_gate, extra: true }, { ...product.human_gate, gate_id: "bad" },
    { ...product.human_gate, question_version: 0 }, { ...product.human_gate, status: "accepted" },
    { ...product.human_gate, work_item_id: domainId }, { ...product.human_gate, work_project_id: undefined },
    { ...product.human_gate, work_project_id: "bad" }, { ...product.human_gate, work_project_id: domainId }]) assert.throws(() => decodeDecision({ ...product, human_gate }, project));
  const moved = { ...product, related_work_item_ids: [], human_gate: { ...product.human_gate, status: "resolved", work_project_id: domainId } };
  assert.equal(decodeDecision(moved, project).human_gate.work_project_id, domainId);
  assert.throws(() => decodeDecision({ ...moved, human_gate: { ...moved.human_gate, work_project_id: project } }, project));
});
