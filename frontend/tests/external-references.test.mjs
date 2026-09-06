import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  normalizeExternalObservation, normalizeExternalReferences, sameExternalReferences,
  validExternalCandidates, validExternalReference, validExternalReferences, validExternalUrl,
  validSparseReferences
} from "../lib/external-references.ts";
import { allowedQueryKeys, browserTransportEffect, invalidMutationBody } from "../lib/proxy-policy.ts";

const fixture = JSON.parse(await readFile(new URL("../../tests/fixtures/external-record-contract-v1.json", import.meta.url), "utf8"));
const reference = { url: "https://example.com/issues/1", kind: "tracked-by", state: "closed" };
const candidate = { url: reference.url, title: "Some work", body: "", state: "open" };
const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const actor = { actor_client: "dashboard", actor_session_id: "references-test" };
const operation = "91b9168a-37d1-4a6a-aa1f-bb538b65cb55";

test("shared URL, Unicode label, and timestamp corpus defines exact reference identity", () => {
  for (const row of fixture.url_cases) assert.equal(validExternalUrl(row.value), row.valid, row.value);
  for (const row of fixture.label_cases) assert.equal(validExternalReference({ ...reference, label: row.value }), row.valid, JSON.stringify(row.value));
  for (const row of fixture.timestamp_cases) assert.equal(normalizeExternalObservation(row.value), row.normalized, row.value);
  for (const value of [null, 1, [], {}, "https://example.com/\ud800"]) assert.equal(validExternalUrl(value), false);
  for (const value of [null, ["tracked-by"], 1]) assert.equal(validExternalReference({ ...reference, kind: value }), false);
  assert.equal(validExternalReference({ ...reference, state_observed_at: "2026-09-05T10:20:00-04:00" }), true);
  assert.equal(validExternalReference({ ...reference, state_observed_at: "2026-09-05T10:20:00-04:00" }, true), false);
});

test("references preserve optional absence, order, exact URL spelling and explicit clear", () => {
  assert.equal(validExternalReferences([]), true);
  const ten = Array.from({ length: 10 }, (_, index) => ({ ...reference, url: `${reference.url}?id=${index}` }));
  assert.equal(validExternalReferences(ten), true);
  assert.equal(validExternalReferences([...ten, { ...reference, url: `${reference.url}/extra` }]), false);
  assert.equal(validExternalReferences([reference, { ...reference, kind: "references" }]), false);
  for (const extra of [{ label: null }, { state_observed_at: null }, { label: "\ud800" }, { extra: true }]) assert.equal(validExternalReference({ ...reference, ...extra }), false);
  assert.equal(validSparseReferences({}), true);
  assert.equal(validSparseReferences({ external_references: [] }), false);
  assert.equal(sameExternalReferences(ten, [...ten].reverse()), false);
  assert.equal(sameExternalReferences(undefined, []), true);
  assert.equal(sameExternalReferences([reference], [{ ...reference, url: reference.url + "#fragment" }]), false);
  assert.equal(normalizeExternalReferences([{ ...reference, state_observed_at: "2026-09-05T10:20:00.120000-04:00" }])[0].state_observed_at, "2026-09-05T14:20:00.12Z");
});

test("candidate population limits, identity and body validation remain separate from persisted references", () => {
  const sixtyFour = Array.from({ length: 64 }, (_, index) => ({ ...candidate, url: `${candidate.url}/${index}` }));
  assert.equal(validExternalCandidates(sixtyFour), true);
  assert.equal(validExternalCandidates([...sixtyFour, candidate]), false);
  assert.equal(validExternalCandidates([candidate, candidate]), false);
  for (const extra of [{ title: "a\nb" }, { title: "a\u202eb" }, { body: null }, { body: "\0" }, { body: "\ud800" }, { title: " " }, { title: "x".repeat(501) }, { body: "😀".repeat(20001) }, { kind: "references" }, { state_observed_at: "2026-09-05T00:00:00Z" }]) assert.equal(validExternalCandidates([{ ...candidate, ...extra }]), false);
  assert.equal(validExternalCandidates([{ ...candidate, body: "Ordinary\nmultiline prose", title: "😀".repeat(500) }]), true);
});

test("proxy accepts reference-only replacement/clear and bounded safe-read candidates without a receipt", () => {
  const path = `projects/${project}/work-items/${work}`;
  for (const refs of [[], [reference]]) assert.equal(invalidMutationBody(path, "PATCH", { expected_version: 1, actor, client_operation_id: operation, external_references: refs }), null);
  for (const refs of [null, [{ ...reference, secret: "x" }], [reference, reference]]) assert.notEqual(invalidMutationBody(path, "PATCH", { expected_version: 1, actor, client_operation_id: operation, external_references: refs }), null);
  assert.ok(allowedQueryKeys(`projects/${project}/work-items`, "GET").includes("external_url"));
  const suggestions = `projects/${project}/duplicate-suggestions`;
  const draft = { title: "Some work", summary: "Draft summary", initial_prompt: "Draft initial prompt", external_candidates: [candidate] };
  assert.equal(invalidMutationBody(suggestions, "POST", draft), null);
  assert.equal(browserTransportEffect(suggestions, "POST"), "safe_read");
  assert.notEqual(invalidMutationBody(suggestions, "POST", { ...draft, client_operation_id: operation }), null);
  assert.notEqual(invalidMutationBody(suggestions, "POST", { ...draft, external_candidates: null }), null);
});
