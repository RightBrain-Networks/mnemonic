import assert from "node:assert/strict";
import test from "node:test";
import { dateBoundsMatch, decodeTagCounts, searchDate, validDateBounds } from "../lib/search-exploration.ts";
import { decodeTermDiagnostics } from "../lib/search-diagnostics.ts";
import { decodeSearchDisclosure, validateSearchDisclosure } from "../lib/search-disclosure.ts";
import { validSearchRequest } from "../lib/search-request.ts";
import { validArtifactSearchRequest } from "../lib/artifacts.ts";
import { validTranscriptQuery } from "../lib/transcript-proxy.ts";
import { disclosure } from "./search-disclosure-fixtures.mjs";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const lower = "2026-09-01T00:00:00.000001Z", upper = "2026-09-01T00:00:00.000002Z";

test("date bounds preserve microseconds, normalize offsets and reject invalid calendars and ranges", () => {
  const shifted = "2026-09-01T01:00:00.000001+01:00";
  assert.equal(searchDate(lower), searchDate(shifted));
  assert.equal(validDateBounds({ created_after: lower, created_before: upper }), true);
  assert.equal(dateBoundsMatch({ created_after: lower }, { created_after: shifted }), true);
  assert.equal(dateBoundsMatch({ created_after: lower }, {}), false);
  for (const bad of ["2026-09-01", "2026-09-01T00:00:00", "2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z", "2026-09-01T24:00:00Z", "2026-09-01T00:00:00+24:00", 123, null]) assert.equal(searchDate(bad), null);
  assert.equal(validDateBounds({ created_after: upper, created_before: lower }), false);
  assert.equal(validDateBounds({ updated_after: lower, updated_before: lower }), false);
  assert.equal(validDateBounds({ created_after: shifted }, true), false);
});

test("unified and dedicated browser request boundaries accept only bounded exploration controls", () => {
  const dates = { created_after: lower, created_before: upper };
  assert.equal(validSearchRequest({ diagnostics: "always", filters: { work_items: dates }, tag_counts: { limit: 20, offset: 40 } }), true);
  assert.equal(validArtifactSearchRequest({ q: "needle", diagnostics: "off", ...dates }), true);
  assert.equal(validTranscriptQuery(new URLSearchParams({ diagnostics: "always", ...dates }), "list"), true);
  for (const body of [{ diagnostics: "automatic" }, { filters: { artifacts: { created_after: "2026-09-01" } } }, { facets: ["transcripts"], tag_counts: {} }, { tag_counts: { limit: 101 } }, { tag_counts: { offset: 1_000_001 } }, { tag_counts: { limit: true } }, { tag_counts: { private: "secret" } }]) assert.equal(validSearchRequest(body), false);
});

test("date disclosure is optional when unused and scope-bound when present", () => {
  const response = disclosure(project, ["work_items"], { q: "needle", diagnostics: "always", filters: { work_items: { created_after: lower, created_before: upper } } });
  const decoded = decodeSearchDisclosure(response, project, ["work_items"]);
  validateSearchDisclosure(decoded, "work_items", { created_after: lower, created_before: upper });
  assert.throws(() => validateSearchDisclosure(decoded, "work_items", {}));
  for (const value of [null, "2026-09-01T00:00:00", "2026-09-01T01:00:00+01:00"]) {
    const bad = structuredClone(response); bad.applied_filters.work_items.created_after = value;
    assert.throws(() => decodeSearchDisclosure(bad, project, ["work_items"]));
  }
  const missing = structuredClone(response); delete missing.diagnostics;
  assert.throws(() => decodeSearchDisclosure(missing, project, ["work_items"]));
});

test("positive term diagnostics require always and never invent counts for unsearched sources", () => {
  const counts = [{ term: "needle", matches: { work_items: 4, artifacts: null, transcripts: null } }];
  assert.deepEqual(decodeTermDiagnostics(counts, 1, ["work_items"], "always", "needle"), counts);
  for (const mode of ["off", "on_empty"]) assert.throws(() => decodeTermDiagnostics(counts, 1, ["work_items"], mode, "needle"));
  assert.throws(() => decodeTermDiagnostics(counts, 0, ["work_items"], "always", " "));
  assert.throws(() => decodeTermDiagnostics([...counts, ...counts], 1, ["work_items"], "always", "needle"));
  assert.throws(() => decodeTermDiagnostics(counts, 1, ["artifacts"], "always", "needle"));
});

test("tag vocabulary enforces counting semantics, Unicode C ordering and independent pagination", () => {
  const page = { items: [{ tag: "release", count: 3 }], total: 3, limit: 1, offset: 1, next_offset: 2, count_unit: "canonical_work_items", member_scope: "returned_work_items", selected_tag_applied: true };
  assert.deepEqual(decodeTagCounts(page, { limit: 1, offset: 1 }, 3), page);
  assert.equal(decodeTagCounts(null, null, 3), null);
  for (const patch of [{ next_offset: null }, { count_unit: "checkpoints" }, { member_scope: "all_aliases" }, { selected_tag_applied: 1 }, { items: [{ tag: "release", count: 4 }] }, { offset: 0 }, { extra: "value" }]) assert.throws(() => decodeTagCounts({ ...page, ...patch }, { limit: 1, offset: 1 }, 3));
  assert.throws(() => decodeTagCounts(page, null, 3));
  const unicode = { ...page, items: [{ tag: "\ue000", count: 1 }, { tag: "\u{10000}", count: 1 }], total: 2, limit: 2, offset: 0, next_offset: null };
  assert.deepEqual(decodeTagCounts(unicode, { limit: 2 }, 3), unicode);
  assert.throws(() => decodeTagCounts({ ...unicode, items: [...unicode.items].reverse() }, { limit: 2 }, 3));
});
