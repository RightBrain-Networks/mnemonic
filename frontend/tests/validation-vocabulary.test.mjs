import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  detailMessage,
  SAFE_VALIDATION_LOCATION_PARTS,
  SAFE_VALIDATION_LOCATION_ROOTS
} from "../lib/api.ts";

const vocabulary = JSON.parse(readFileSync(
  new URL("../../docs/validation-vocabulary.json", import.meta.url), "utf8"
));
const corpus = JSON.parse(readFileSync(
  new URL("../../tests/fixtures/validation-locations-v1.json", import.meta.url), "utf8"
));

test("browser validation fields and HTTP roots match their reviewed subsets", () => {
  assert.deepEqual(
    [...SAFE_VALIDATION_LOCATION_PARTS].sort(),
    [...vocabulary.common_fields, ...vocabulary.surface_fields.browser].sort()
  );
  assert.deepEqual(
    [...SAFE_VALIDATION_LOCATION_ROOTS].sort(),
    vocabulary.browser_location_roots
  );
});

for (const item of corpus.cases) {
  test(`browser shared validation location corpus: ${item.id}`, () => {
    // Message text is already sanitized by the backend/proxy. The browser owns
    // location presentation and must ignore raw input/context if supplied.
    const msg = item.backend_message ?? "Value is invalid.";
    const expected = item.browser_location ? `${item.browser_location}: ${msg}` : msg;
    for (const loc of [item.location, item.backend_location]) {
      const result = detailMessage([{
        loc,
        msg,
        type: item.type ?? "value_error",
        input: { PRIVATE_CALLER_KEY: "PRIVATE_CALLER_VALUE" },
        ctx: { error: "PRIVATE_CALLER_VALUE" }
      }]);
      assert.deepEqual(result, { message: expected });
      for (const marker of corpus.private_markers) {
        assert.equal(result.message.includes(marker), false);
      }
    }
  });
}
