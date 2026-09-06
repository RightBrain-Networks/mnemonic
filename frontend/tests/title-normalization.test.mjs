import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { nfkcUnicode15_1 } from "../lib/title-normalization.ts";
import { decodeDuplicateSuggestionPage } from "../lib/duplicate-suggestions.ts";

const fixture = JSON.parse(await readFile(new URL("../../tests/fixtures/external-record-contract-v1.json", import.meta.url), "utf8"));

test("browser Unicode 16/17 additions remain inert under PostgreSQL 15.1 normalization", () => {
  for (const { value, key } of fixture.title_key_cases) {
    const actual = nfkcUnicode15_1(value)
      .replace(/^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g, "")
      .replace(/[\t\n\v\f\r ]+/g, " ")
      .replace(/[A-Z]/g, (character) => character.toLowerCase());
    assert.equal(actual, key, JSON.stringify(value));
  }
  // Node 24 uses newer Unicode; this is an observable native/runtime disagreement.
  assert.equal("\u{1ccd6}".normalize("NFKC"), "A");
  assert.equal("\ua7f1".normalize("NFKC"), "S");
  assert.notEqual("a\u0315\u1acf\u0300".normalize("NFKC"), "a\u0315\u1acf\u0300");
});

for (const [title, candidateTitle, exact] of [
  ["A", "\u{1ccd6}", false], ["\u{1ccd6}", "\u{1ccd6}", true],
  ["S", "\ua7f1", false], ["\ua7f1", "\ua7f1", true]
]) {
  test(`request-bound browser guard accepts SQL title identity ${JSON.stringify([title, candidateTitle])}`, () => {
    const reference = { url: "https://example.com/1", title: candidateTitle, state: "open" };
    const request = { title, summary: "the", initial_prompt: "and", limit: 5,
      external_candidates: [{ ...reference, body: "" }] };
    const page = { items: [], limit: 5, mode: "lexical", semantic_available: false,
      semantic_scope: "unavailable", composition_version: "duplicate-suggestion-v1",
      exact_title_group_total: 0, omitted_exact_title_group_count: 0,
      external_candidate_count: 1, external_scope: "lexical",
      external_items: exact ? [{ rank: 1, signals: ["exact_title"], reference }] : [] };
    assert.deepEqual(decodeDuplicateSuggestionPage(page, request), page);
    if (!exact) assert.throws(() => decodeDuplicateSuggestionPage({ ...page,
      external_items: [{ rank: 1, signals: ["exact_title"], reference }] }, request));
  });
}
