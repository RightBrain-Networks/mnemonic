import assert from "node:assert/strict";
import test from "node:test";
import { validSearchRequest } from "../lib/search-request.ts";
import { validArtifactSearchRequest } from "../lib/artifacts.ts";
import { validTranscriptQuery } from "../lib/transcript-proxy.ts";
import { childSearchParams, workSearchParams } from "../lib/work-item-search.ts";
import { artifactSearchRequest, transcriptSearchRequest, workSearchRequest, decodeUnifiedWorkSearchPage } from "../lib/unified-search.ts";
import { disclosure } from "./search-disclosure-fixtures.mjs";
import { TRANSCRIPT_SEARCH_HINT } from "../lib/search-diagnostics.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const options = { status: "all", sort: "updated", limit: 20, offset: 0, query: "needle" };

test("dashboard requests full summaries explicitly and child browsing has no search detail parameter", () => {
  for (const body of [workSearchRequest(options), artifactSearchRequest("needle", true, false, 50, 0), transcriptSearchRequest("needle", true, 0)]) {
    assert.equal(body.detail, "full");
    assert.equal(validSearchRequest(body), true);
  }
  assert.equal(workSearchParams(options).get("detail"), "full");
  assert.equal(childSearchParams(options).has("detail"), false);
});

test("browser search boundaries allow only the two supported detail modes", () => {
  for (const detail of ["compact", "full"]) {
    assert.equal(validSearchRequest({ detail }), true);
    assert.equal(validArtifactSearchRequest({ q: "needle", detail }), true);
    assert.equal(validTranscriptQuery(new URLSearchParams({ detail }), "list"), true);
  }
  for (const detail of ["pointer", "", null, true]) {
    assert.equal(validSearchRequest({ detail }), false);
    assert.equal(validArtifactSearchRequest({ q: "needle", detail }), false);
    assert.equal(validTranscriptQuery(new URLSearchParams({ detail: String(detail) }), "list"), false);
  }
});

test("dashboard rejects a compact or unspecified substitute even when a work result is empty", () => {
  const page = { ...disclosure(project, ["work_items"]), detail: "full", work_rank_scope: "work_items", items: [], total: 0, limit: 50, offset: 0, search_scope: { searched_facets: ["work_items"], transcripts: "not_selected", transcript_search_hint: TRANSCRIPT_SEARCH_HINT }, term_diagnostics: [], facet_totals: { work_items: 0, artifacts: 0, transcripts: 0 }, coverage: { artifacts: { enabled: true }, transcripts: { indexing_incomplete: false } }, indexing_incomplete: false };
  assert.equal(decodeUnifiedWorkSearchPage(page, project).detail, "full");
  assert.throws(() => decodeUnifiedWorkSearchPage({ ...page, detail: "compact" }, project));
  const missing = { ...page }; delete missing.detail;
  assert.throws(() => decodeUnifiedWorkSearchPage(missing, project));
  assert.throws(() => decodeUnifiedWorkSearchPage({ ...page, work_rank_scope: "global" }, project));
});
