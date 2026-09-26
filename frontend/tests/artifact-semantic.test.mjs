import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";
import { artifactPassageQuery, decodeArtifactPassageText, decodeEmbeddingCoverage, verifyArtifactPassageIds } from "../lib/artifact-semantic.ts";
import { decodeArtifactSearchPage, validArtifactSearchRequest } from "../lib/artifacts.ts";
import { artifactSearchRequest, decodeUnifiedArtifactSearchPage } from "../lib/unified-search.ts";
import { validSearchRequest } from "../lib/search-request.ts";
import { artifactQueryKeys, proxyArtifact } from "../lib/artifact-proxy.ts";
import { disclosure, projectCoverage, withholding } from "./search-disclosure-fixtures.mjs";
import { ranking, unifiedRanking } from "./search-ranking-fixtures.mjs";
import { TRANSCRIPT_SEARCH_HINT } from "../lib/search-diagnostics.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8", id = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const timestamp = "2026-09-16T00:00:00Z", q = "remote listener refreshes sessions";
const text = "🔑 Renew the cookie before the remote listener session expires. <script>untrusted</script>";
const extraction = { status: "ready", truncated: false, error_code: null, extracted_at: timestamp };
const artifact = { id, project_id: project, filename: "design.txt", description: "Listener design", revision: 3, size_bytes: 8000, sha256: "a".repeat(64), mime_type: "text/plain", created_at: timestamp, modified_at: timestamp, deleted_at: null, content_available: true, sensitive: false, related_artifact_ids: [], related_work_item_ids: [], originating_work_item_id: null, created_by_agent_session_id: null, extraction: { ...extraction, metadata: {} } };
const coverage = { model: "model-🔑", chunk_config: "tokenizer-v2", state: "ready", ready: 1, pending: 0, processing: 0, failed: 0, unavailable: 0, withheld: 0, empty: 0, passages: 9, truncated: 0, total_kind: "ranked_candidates", score_type: "semantic_reciprocal_rank" };
const passage = { passage_id: "", artifact_revision: 3, text_sha256: "b".repeat(64), start_offset: 7000, end_offset: 7000 + Array.from(text).length, model: coverage.model, chunk_config: coverage.chunk_config, token_count: 23, token_limit: 512, cosine_similarity: 0.75, score_is_probability: false, score_type: "cosine_similarity" };
const key = JSON.stringify([id, 3, passage.text_sha256, passage.model, passage.chunk_config, passage.start_offset, passage.end_offset]).replace(/[\x7f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
passage.passage_id = createHash("sha256").update(key).digest("hex");
const match = { artifact, passage, snippet: text, matched_fields: ["content"], evidence: "semantic", rank: 1, score: 1 / 61, score_type: "semantic_reciprocal_rank" };
const options = { q, fulltext: true, filters: { artifacts: { semantic: true } }, semantic: true };
const page = { ...ranking(q, "artifacts", true), ...disclosure(project, ["artifacts"], options), next_offset: null, page_truncated: false, detail: "full", match_mode: "semantic_passages", embedding: coverage, items: [match], total: 1, limit: 50, offset: 0, fulltext: true, term_diagnostics: [], indexing: { ready: 1, pending: 0, failed: 0, truncated: 0 }, sensitive_content_withheld: 0 };
const decode = (value = page) => decodeArtifactSearchPage(value, project, true, 50, 0, true, "terms", true);

test("semantic artifact passages bind current evidence, Unicode offsets and deterministic identity", async () => {
  const result = decode();
  assert.equal(result.items[0].passage.end_offset - 7000, Array.from(text).length);
  await verifyArtifactPassageIds(result.items);
  await assert.rejects(verifyArtifactPassageIds([{ ...result.items[0], passage: { ...passage, passage_id: "c".repeat(64) } }]), /different source/);
  for (const change of [{ artifact_revision: 4 }, { text_sha256: "invalid" }, { model: "other" }, { chunk_config: "other" }, { start_offset: passage.end_offset }, { end_offset: 8501 }, { token_count: 513 }, { cosine_similarity: 1.01 }, { score_type: "probability" }, { score_is_probability: true }]) assert.throws(() => decode({ ...page, items: [{ ...match, passage: { ...passage, ...change } }] }));
  for (const change of [{ evidence: "lexical" }, { passage: null }, { matched_fields: ["metadata"] }, { snippet: "unrelated" }, { artifact: { ...artifact, deleted_at: timestamp } }, { artifact: { ...artifact, extraction: { ...artifact.extraction, status: "pending" } } }]) assert.throws(() => decode({ ...page, items: [{ ...match, ...change }] }));
  assert.throws(() => decodeArtifactSearchPage(page, project, true));
});

test("embedding coverage distinguishes pending vectors, withheld bodies and unavailable inference", () => {
  for (const field of ["pending", "processing", "failed", "unavailable", "withheld", "truncated"]) {
    const embedding = { ...coverage, [field]: 1, state: "incomplete" };
    const vectors = !["withheld", "truncated"].includes(field);
    const semantic = { ...page.semantic, partial_vectors: vectors, comparison_incomplete: vectors };
    assert.equal(decode(withholding({ ...page, embedding, semantic, sensitive_content_withheld: embedding.withheld })).embedding[field], 1);
    assert.throws(() => decode({ ...page, embedding: { ...embedding, state: "ready" }, semantic }));
  }
  assert.throws(() => decode({ ...page, embedding: { ...coverage, ready: 0, passages: 0 } }));
  assert.throws(() => decode({ ...page, embedding: { ...coverage, failed: 1, state: "incomplete" } }));
  assert.throws(() => decodeEmbeddingCoverage({ ...coverage, ready: 0 }));
  assert.throws(() => decodeEmbeddingCoverage({ ...coverage, ready: true }));
  assert.throws(() => decodeEmbeddingCoverage({ ...coverage, state: "incomplete", truncated: 2, withheld: 5 }));
  assert.equal(decodeEmbeddingCoverage({ ...coverage, ready: 0, passages: 0, unavailable: 1, state: "unavailable" }).state, "unavailable");
});

test("unified semantic artifact pages retain source ranking, project ownership and incomplete coverage", () => {
  const unified = projectCoverage({ ...page, ...unifiedRanking(["artifacts"], options), work_rank_scope: "work_items", tag_counts: null,
    search_scope: { searched_facets: ["artifacts"], transcripts: "not_selected", transcript_search_hint: TRANSCRIPT_SEARCH_HINT },
    facet_totals: { work_items: 0, artifacts: 1, transcripts: 0 }, items: [{ facet: "artifacts", id, project_id: project, rank: 1, score: 1 / 61, score_type: "unified_reciprocal_rank", created_at: timestamp, updated_at: timestamp, artifact: match }],
    coverage: { artifacts: { enabled: true, indexing: page.indexing, sensitive_content_withheld: 0, embedding: coverage }, transcripts: { indexing_incomplete: false } }, indexing_incomplete: false }, project);
  const read = (value) => decodeUnifiedArtifactSearchPage(value, project, true, 50, 0, false, undefined, q, "terms", true);
  assert.equal(read(unified).items[0].score_type, "semantic_reciprocal_rank");
  assert.throws(() => read({ ...unified, items: [{ ...unified.items[0], project_id: id }] }));
  assert.throws(() => read({ ...unified, project_coverage: [{ ...unified.project_coverage[0], project_id: id }] }));
  const incomplete = structuredClone(unified);
  incomplete.coverage.artifacts.embedding.withheld = 1; incomplete.coverage.artifacts.embedding.state = "incomplete"; incomplete.coverage.artifacts.sensitive_content_withheld = 1;
  projectCoverage(incomplete, project);
  assert.throws(() => read(incomplete));
  incomplete.indexing_incomplete = true; projectCoverage(incomplete, project);
  assert.equal(read(incomplete).embedding.withheld, 1);
});

test("semantic requests require explicit contents and an unconstrained nonblank query", () => {
  const request = artifactSearchRequest(q, true, false, 50, 0, undefined, true);
  assert.equal(validSearchRequest(request), true);
  assert.equal(validArtifactSearchRequest({ q, fulltext: true, semantic: true }), true);
  for (const change of [{ fulltext: false }, { q: "" }, { q: '"session cookie"' }, { query_mode: "literal" }, { query_mode: "phrase" }]) {
    assert.equal(validArtifactSearchRequest({ q, fulltext: true, semantic: true, ...change }), false);
    assert.equal(validSearchRequest({ ...request, ...change }), false);
  }
  assert.equal(validSearchRequest({ ...request, facets: ["work_items"] }), false);
});

test("passage text reads pin both source revision and extracted text hash", () => {
  const query = artifactPassageQuery(passage);
  assert.equal(query.get("expected_text_sha256"), passage.text_sha256);
  const response = { project_id: project, artifact_id: id, revision: 3, sha256: artifact.sha256, text_sha256: passage.text_sha256, extraction, text, offset: 7000, limit: Array.from(text).length, total_chars: passage.end_offset + 100, next_offset: passage.end_offset };
  assert.equal(decodeArtifactPassageText(response, match), text);
  for (const change of [{ project_id: id }, { artifact_id: project }, { revision: 4 }, { sha256: "c".repeat(64) }, { text_sha256: "c".repeat(64) }, { offset: 0 }, { next_offset: null }, { total_chars: 7001 }, { text: "Wrong snapshot" }, { extraction: { ...extraction, status: "pending" } }]) assert.throws(() => decodeArtifactPassageText({ ...response, ...change }, match));
});

test("artifact passage proxy permits only bounded pinned reads and forwards no authority from the browser", async () => {
  const path = `projects/${project}/artifacts/${id}/text`;
  assert.deepEqual(artifactQueryKeys(path, "GET"), ["expected_revision", "expected_text_sha256", "offset", "limit"]);
  const query = artifactPassageQuery(passage), calls = [];
  const fetcher = async (url, init) => { calls.push(String(url)); assert.equal(init.headers.get("X-Artifact-Access"), "human-dashboard"); return Response.json({ text }); };
  const environment = { MNEMONIC_API_KEY: "a".repeat(64), MNEMONIC_API_URL: "http://api:8000" };
  const read = (suffix) => proxyArtifact(new Request(`http://localhost:3000/api/artifacts/${path}?${suffix}`, { headers: { host: "localhost:3000" } }), path.split("/"), environment, fetcher);
  assert.equal((await read(query)).status, 200);
  assert.equal(new URL(calls[0]).searchParams.get("expected_text_sha256"), passage.text_sha256);
  for (const [field, value] of [["expected_text_sha256", ""], ["expected_revision", "0"], ["offset", "8000001"], ["limit", "20001"], ["access_as_human", "true"]]) {
    const invalid = new URLSearchParams(query); invalid.set(field, value); assert.equal((await read(invalid)).status, 400);
  }
  assert.equal(calls.length, 1);
});


test("disabled artifact semantic search reports no inference and cannot manufacture embedding coverage", () => {
  const disabled = projectCoverage({ ...unifiedRanking([], { q }), ...disclosure(project, [], { q }), next_offset: null, page_truncated: false, detail: "full", work_rank_scope: "work_items", tag_counts: null,
    total: 0, items: [], limit: 50, offset: 0, term_diagnostics: [], facet_totals: { work_items: 0, artifacts: 0, transcripts: 0 },
    search_scope: { searched_facets: [], transcripts: "not_selected", transcript_search_hint: TRANSCRIPT_SEARCH_HINT },
    coverage: { artifacts: { enabled: false, indexing: { ready: 0, pending: 0, failed: 0, truncated: 0 }, sensitive_content_withheld: 0, embedding: null }, transcripts: { indexing_incomplete: false } }, indexing_incomplete: true }, project);
  const read = (value) => decodeUnifiedArtifactSearchPage(value, project, true, 50, 0, false, undefined, q, "terms", true);
  assert.equal(read(disabled).semantic.inference.status, "not_requested");
  const forged = structuredClone(disabled); forged.coverage.artifacts.embedding = coverage; projectCoverage(forged, project);
  assert.throws(() => read(forged));
});
