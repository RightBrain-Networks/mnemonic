import assert from "node:assert/strict";
import test from "node:test";
import { readDecisionFile, decisionWriteBody, decodeDeletionPreview, deletionNeedsAcknowledgement, maximumDecisionBytes, verifyDeletionResult, downloadKnowledgeFile } from "../lib/knowledge-management.ts";

const project = "1b7f6231-9bb8-45a5-b98e-865cd2b5fd70";
const domain = "2b7f6231-9bb8-45a5-b98e-865cd2b5fd70";

test("uploads validate file extension, UTF-8, byte bound and null characters before mutation", async () => {
  const read = await readDecisionFile(new File(["# Café\n"], "Policy.MD"));
  assert.deepEqual(read, { title: "Policy", content: "# Café\n", media_type: "text/markdown" });
  assert.equal((await readDecisionFile(new File(["plain"], "policy.txt"))).media_type, "text/plain");
  await assert.rejects(readDecisionFile(new File(["script"], "policy.html")), /Markdown/);
  await assert.rejects(readDecisionFile(new File([new Uint8Array([0xff, 0xfe])], "policy.txt")), /UTF-8/);
  await assert.rejects(readDecisionFile(new File(["bad\0text"], "policy.txt")), /null characters/);
  await assert.rejects(readDecisionFile(new File([new Uint8Array(maximumDecisionBytes + 1)], "policy.md")), /1 MiB/);
});

test("human updates pin the read revision and retain all existing graph metadata", () => {
  const draft = { domainId: domain, title: " Human edit ", content: "Merged human and agent changes.", mediaType: "text/markdown", state: "accepted" };
  const basis = { domain_id: domain, decision_id: "ADR-00001", revision: 8,
    related_decision_ids: ["PDR-00002"], related_work_item_ids: [project], related_artifact_ids: [domain],
    supersedes: ["ADR-00003"], depends_on: [{ decision_id: "PDR-00002", revision: 1, rationale: "Owner choice" }],
    supersession_details: [{ decision_id: "ADR-00003", revision: 0, extent: "partial", reason: "Transport only" }] };
  const body = decisionWriteBody(draft, basis);
  assert.equal(body.expected_revision, 8);
  assert.equal(body.author_kind, "human");
  assert.equal(body.title, "Human edit");
  assert.equal(body.source_work_item_id, null);
  for (const key of ["related_decision_ids", "related_work_item_ids", "related_artifact_ids", "supersedes", "depends_on", "supersession_details"]) assert.deepEqual(body[key], basis[key]);
  assert.match(body.client_operation_id, /^[0-9a-f-]{36}$/);
  const fresh = decisionWriteBody(draft);
  assert.equal(Object.hasOwn(fresh, "expected_revision"), false);
  assert.throws(() => decisionWriteBody({ ...draft, content: "é".repeat(maximumDecisionBytes) }), /1 MiB/);
});

test("deletion previews bind identity and only relationships require the acknowledgement", () => {
  const preview = { project_id: project, domain_id: domain, decision_id: null, document_count: 8,
    relationship_count: 0, removed_relationship_count: 0, affected_review_count: 0, relationships: {}, expected_version: 2, expected_revision: null,
    preview_token: "a".repeat(64), blocked_reason: null };
  const target = { kind: "domain", id: domain, name: "Architecture" };
  assert.deepEqual(decodeDeletionPreview(preview, project, target), preview);
  assert.equal(deletionNeedsAcknowledgement(preview), false);
  assert.equal(deletionNeedsAcknowledgement({ ...preview, relationship_count: 3 }), true);
  for (const changed of [{ project_id: domain }, { domain_id: project }, { decision_id: "ADR-00001" },
    { preview_token: "stale" }, { relationship_count: -1 }, { relationships: { work: "3" } }, { blocked_reason: false }]) {
    assert.throws(() => decodeDeletionPreview({ ...preview, ...changed }, project, target));
  }
  const decision = { ...preview, decision_id: "ADR-00001", expected_version: null, expected_revision: 0, document_count: 1 };
  assert.equal(decodeDeletionPreview(decision, project, { kind: "decision", id: "ADR-00001", name: "Policy" }).expected_revision, 0);
});

test("deletion success must prove the exact reviewed project, target and graph snapshot", () => {
  const preview = { project_id: project, domain_id: domain, decision_id: "ADR-00001", document_count: 1,
    relationship_count: 2, removed_relationship_count: 2, affected_review_count: 0, relationships: { work_links: 2 }, expected_version: null, expected_revision: 0,
    preview_token: "a".repeat(64), blocked_reason: null };
  const result = { ...preview, deleted_at: "2026-10-25T19:00:00Z" };
  assert.doesNotThrow(() => verifyDeletionResult(result, preview));
  for (const changed of [{ project_id: domain }, { domain_id: project }, { decision_id: "ADR-00002" },
    { preview_token: "b".repeat(64) }, { document_count: 2 }, { relationship_count: 3 }, { relationships: {} },
    { deleted_at: "yesterday" }]) assert.throws(() => verifyDeletionResult({ ...result, ...changed }, preview));
});

test("native downloads stream chunks to disk and abort incomplete files", async () => {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch;
  const chunks = []; let closed = false, aborted = false;
  globalThis.window = { showSaveFilePicker: async ({ suggestedName }) => {
    assert.equal(suggestedName, "ADR-decisions.tar.gz");
    return { createWritable: async () => ({ write: async (chunk) => chunks.push([...chunk]), close: async () => { closed = true; }, abort: async () => { aborted = true; } }) };
  } };
  try {
    globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new Uint8Array([1, 2])); controller.enqueue(new Uint8Array([3, 4])); controller.close();
    } }));
    await downloadKnowledgeFile("/export", "ADR-decisions.tar.gz");
    assert.deepEqual(chunks, [[1, 2], [3, 4]]); assert.equal(closed, true); assert.equal(aborted, false);
    closed = false;
    globalThis.fetch = async () => new Response(new ReadableStream({ start(controller) { controller.error(new Error("Disconnected")); } }));
    await assert.rejects(downloadKnowledgeFile("/export", "ADR-decisions.tar.gz"), /Disconnected/);
    assert.equal(closed, false); assert.equal(aborted, true);
  } finally { globalThis.window = originalWindow; globalThis.fetch = originalFetch; }
});
