import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { detailMessage } from "../lib/api.ts";
import { decodeSearchDisclosure, validateSearchDisclosure } from "../lib/search-disclosure.ts";
import { decodeHierarchyPage, decodeHierarchySearchPage } from "../lib/hierarchy-presentation.ts";
import { decodeUnifiedWorkSearchPage } from "../lib/unified-search.ts";
import { VALIDATION_RULES } from "../lib/validation-rules.ts";
import { disclosure } from "./search-disclosure-fixtures.mjs";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const other = "e36a7e53-938f-4c8a-b75a-af9c7331711a";

test("quoted query disclosure retains exact source semantics, null sources and static warnings", () => {
  const response = disclosure(project, ["work_items", "artifacts"], { q: '"admission cookie"', fulltext: true });
  assert.deepEqual(decodeSearchDisclosure(response, project, ["work_items", "artifacts"]), response);
  assert.equal(response.applied_filters.work_items.status, "all");
  assert.equal(response.query_interpretation.work_items.fulltext, null);
  assert.equal(response.query_interpretation.artifacts.fulltext, true);
  assert.equal(response.query_interpretation.transcripts, null);
  assert.equal(response.warnings[0].code, "phrase_operators_ignored");
  const reordered = structuredClone(response);
  const warning = reordered.warnings[0];
  reordered.warnings = [{ message: warning.message, sources: warning.sources, code: warning.code }];
  assert.deepEqual(decodeSearchDisclosure(reordered, project, ["work_items", "artifacts"]), reordered);
});

test("search disclosure rejects missing, unsupported and misleading metadata even on empty pages", () => {
  const response = disclosure(project, ["work_items"], { q: '"error line"' });
  const invalid = [];
  for (const field of ["applied_filters", "query_interpretation", "warnings"]) {
    const next = structuredClone(response); delete next[field]; invalid.push(next);
  }
  for (const update of [
    (next) => { next.applied_filters.project_id = other; },
    (next) => { delete next.applied_filters.work_items.status; },
    (next) => { next.applied_filters.work_items.status = "unknown"; },
    (next) => { next.query_interpretation.work_items.fields = ["title"]; },
    (next) => { next.query_interpretation.work_items.fulltext = false; },
    (next) => { next.query_interpretation.work_items.match_mode = "phrase"; },
    (next) => { next.warnings = []; },
    (next) => { next.warnings[0].message = "private-upstream-message"; },
    (next) => { next.warnings[0].sources = ["transcripts"]; }
  ]) { const next = structuredClone(response); update(next); invalid.push(next); }
  for (const next of invalid) assert.throws(() => decodeSearchDisclosure(next, project, ["work_items"]));
});

test("empty work search validates effective status and preserves it in returned page", () => {
  const metadata = disclosure(project, ["work_items"], { q: "lease", filters: { work_items: { status: "done" } } });
  const page = { ...metadata, search_scope: { searched_facets: ["work_items"], transcripts: "not_selected", transcript_search_hint: 'Agent sessions can be searched by explicitly including "transcripts" in facets or calling search_transcript_contents.' }, term_diagnostics: [], items: [], total: 0, limit: 50, offset: 0, facet_totals: { work_items: 0, artifacts: 0, transcripts: 0 }, coverage: { artifacts: { enabled: true }, transcripts: { indexing_incomplete: false } }, indexing_incomplete: false };
  const options = { query: "lease", expectedFilters: { status: "done" } };
  assert.equal(decodeUnifiedWorkSearchPage(page, project, options).applied_filters.work_items.status, "done");
  assert.throws(() => decodeUnifiedWorkSearchPage(page, project, { ...options, expectedFilters: { status: "pending" } }));
  assert.throws(() => decodeUnifiedWorkSearchPage(page, project, { ...options, query: "other" }));
  assert.throws(() => validateSearchDisclosure(metadata, "work_items", { source_session_id: "opaque" }));
});

test("hierarchy search requires disclosure while child hierarchy listing keeps its existing shape", () => {
  const page = { items: [], total: 0, limit: 20, offset: 0 };
  assert.deepEqual(decodeHierarchyPage(page, project, 20, 0), page);
  assert.throws(() => decodeHierarchySearchPage(page, project, 20, 0));
  const metadata = disclosure(project, ["work_items"], { filters: { work_items: { status: "pending", view: "roots" } } });
  assert.deepEqual(decodeHierarchySearchPage({ ...page, ...metadata }, project, 20, 0, { status: "pending" }), { ...page, ...metadata });
  assert.throws(() => decodeHierarchySearchPage({ ...page, ...metadata }, project, 20, 0, { status: "done" }));
});

test("reviewed validation rules match the public catalog and never render arbitrary upstream messages", () => {
  const vocabulary = JSON.parse(readFileSync(new URL("../../docs/validation-vocabulary.json", import.meta.url), "utf8"));
  assert.deepEqual(VALIDATION_RULES, Object.fromEntries(Object.entries(vocabulary.rules).map(([code, rule]) => [code, [rule.field, rule.message]])));
  for (const [code, [field, message]] of Object.entries(VALIDATION_RULES)) {
    assert.deepEqual(detailMessage([{ type: code, loc: ["query", "external_url"], msg: "private-upstream-message", input: "private-input-value" }]), { message: `${field ?? "external_url"}: ${message}` });
  }
});
