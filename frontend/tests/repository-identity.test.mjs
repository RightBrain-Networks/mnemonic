import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { canonicalRepository } from "../lib/decision-provenance.ts";

const cases = JSON.parse(readFileSync(new URL("../../backend/tests/fixtures/repository-identities.json", import.meta.url), "utf8"));
for (const [value, expected] of cases.filter(([value]) => value !== null)) {
  test(`repository identity is conservative and idempotent: ${JSON.stringify(value)}`, () => {
    assert.equal(canonicalRepository(value), expected);
    assert.equal(canonicalRepository(expected), expected);
  });
}
