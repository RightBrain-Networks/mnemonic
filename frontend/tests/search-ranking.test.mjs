import assert from "node:assert/strict";
import test from "node:test";
import { decodeSemantic, decodeHitRanking, decodeSearchRanking, validateSourceRanking, comparisonNotice } from "../lib/search-ranking.ts";
import { decodeWorkEvidence } from "../lib/search-evidence.ts";
import { validSearchRequest } from "../lib/search-request.ts";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import { decodeSearchDisclosure } from "../lib/search-disclosure.ts";
import { ranking, hitRanking, semanticDisposition, evidence } from "./search-ranking-fixtures.mjs";
const member = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const checkpoint = "e36a7e53-938f-4c8a-b75a-af9c7331711a";

test("semantic availability, partial scope, retry and cache refresh remain independent", () => {
  for (const scope of ["not_requested", "full_scope", "lexical_shortlist", "unavailable"]) assert.deepEqual(decodeSemantic(semanticDisposition(scope)), semanticDisposition(scope));
  const ready = semanticDisposition("full_scope");
  const cacheFailure = { ...ready, cache_refresh: { status: "failed", reason: "cache_refresh_failed" } };
  assert.equal(comparisonNotice(decodeSemantic(cacheFailure)), null);
  assert.match(comparisonNotice(decodeSemantic(semanticDisposition("unavailable"))), /cannot rule out a duplicate/);
  assert.match(comparisonNotice(decodeSemantic(semanticDisposition("lexical_shortlist"))), /only a text shortlist/);
  for (const patch of [{ inference: { status: "unavailable", reason: null } }, { partial_vectors: true }, { candidate_scope: "lexical_shortlist" }, { comparison_incomplete: true }, { retry: { max_attempts: 20, after_seconds: 1 } }, { cache_refresh: { status: "failed", reason: "private upstream text" } }, { unexpected: "ignored" }]) assert.throws(() => decodeSemantic({ ...ready, ...patch }));
  const unavailable = semanticDisposition("unavailable");
  assert.throws(() => decodeSemantic({ ...unavailable, comparison_incomplete: false }));
  assert.throws(() => decodeSemantic({ ...unavailable, retry: null }));
});

test("native score meaning is bound to query mode and candidate totals", () => {
  const work = decodeSearchRanking(ranking("needle"));
  validateSourceRanking(work, "needle", "work_items", "postgresql_plain_terms_or_substring");
  for (const patch of [{ score_type: "cosine_similarity" }, { total_kind: "ranked_candidates" }, { semantic: semanticDisposition("full_scope") }]) assert.throws(() => validateSourceRanking(decodeSearchRanking({ ...work, ...patch }), "needle", "work_items", "postgresql_plain_terms_or_substring"));
  validateSourceRanking(decodeSearchRanking(ranking("error[42]", "artifacts", false, "literal")), "error[42]", "artifacts", "literal");
  for (const patch of [{ score_type: "probability" }, { rank: 0 }, { rank: 6 }, { score: NaN }]) assert.throws(() => decodeHitRanking({ ...hitRanking("needle"), ...patch }, "postgresql_lexical", 5));
});

test("checkpoint evidence retains its member and bounded original text", () => {
  const text = '<script>untrusted historical content</script>';
  const valid = { evidence_mode: "lexical", matched_fields: ["checkpoint"], excerpts: [{ field: "checkpoint", text, matched_member_id: member, checkpoint_id: checkpoint, match_type: "phrase" }], excerpts_truncated: false };
  assert.deepEqual(decodeWorkEvidence(valid, member, "lexical"), valid);
  assert.deepEqual(decodeWorkEvidence(valid, member, "semantic"), valid);
  for (const patch of [{ matched_member_id: checkpoint }, { checkpoint_id: null }, { field: "summary" }, { text: "x".repeat(321) }, { match_type: "semantic" }]) assert.throws(() => decodeWorkEvidence({ ...valid, excerpts: [{ ...valid.excerpts[0], ...patch }] }, member, "lexical"));
  assert.throws(() => decodeWorkEvidence({ ...valid, matched_fields: ["summary", "checkpoint"], excerpts: [{ ...valid.excerpts[0], text: "x".repeat(200) }, { ...valid.excerpts[0], field: "summary", checkpoint_id: null, text: "x".repeat(200) }] }, member, "lexical"));
  assert.throws(() => decodeWorkEvidence(valid, member, "lexical", ["title", "summary"]));
  assert.throws(() => decodeWorkEvidence({ ...evidence(member, "semantic"), excerpts: valid.excerpts }, member, "semantic"));
});

test("explicit phrases and literal queries preserve mode and selected fields through browser guards", () => {
  for (const mode of ["terms", "phrase", "literal"]) {
    assert.equal(validSearchRequest({ q: "lease cookie", query_mode: mode, facets: ["work_items"], filters: { work_items: { work_fields: ["title", "summary"] } } }), true);
    const value = disclosure(member, ["work_items"], { q: mode === "terms" ? '"lease cookie"' : "lease cookie", query_mode: mode, filters: { work_items: { work_fields: ["title", "summary"] } } });
    assert.deepEqual(decodeSearchDisclosure(value, member, ["work_items"]), value);
  }
  assert.equal(validSearchRequest({ q: "lease", query_mode: "regex" }), false);
  assert.equal(validSearchRequest({ q: "lease", filters: { work_items: { work_fields: null } } }), false);
  assert.equal(validSearchRequest({ q: "lease", filters: { work_items: { work_fields: ["private"] } } }), false);
});
