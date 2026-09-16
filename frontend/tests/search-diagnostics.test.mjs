import assert from "node:assert/strict";
import test from "node:test";
import { decodeTermDiagnostics } from "../lib/search-diagnostics.ts";

test("term diagnostics preserve zero versus an unsearched source", () => {
  const diagnostics = [{ term: "fastapi", matches: { work_items: null, artifacts: 2, transcripts: null } },
    { term: "absent", matches: { work_items: null, artifacts: 0, transcripts: null } }];
  assert.deepEqual(decodeTermDiagnostics(diagnostics, 0, ["artifacts"]), diagnostics);
  assert.throws(() => decodeTermDiagnostics(diagnostics, 1, ["artifacts"]));
  assert.throws(() => decodeTermDiagnostics(diagnostics, 0, ["artifacts", "transcripts"]));
  for (const count of [null, true, -1, "2", Infinity]) {
    assert.throws(() => decodeTermDiagnostics([{ term: "fastapi", matches: { ...diagnostics[0].matches, artifacts: count } }], 0, ["artifacts"]));
  }
  assert.throws(() => decodeTermDiagnostics([diagnostics[0], diagnostics[0]], 0, ["artifacts"]));
  assert.throws(() => decodeTermDiagnostics([{ ...diagnostics[0], term: "" }], 0, ["artifacts"]));
});
