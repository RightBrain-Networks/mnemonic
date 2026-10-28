import assert from "node:assert/strict";
import test from "node:test";
import { exportSelection } from "../lib/project-export.ts";

const domain = "11111111-1111-4111-8111-111111111111";
const artifact = "22222222-2222-4222-8222-222222222222";
test("exports require an explicit valid selection and support each asset type independently", () => {
  assert.deepEqual(exportSelection([domain], []), { domain_ids: [domain], artifact_ids: [] });
  assert.deepEqual(exportSelection([], [artifact]), { domain_ids: [], artifact_ids: [artifact] });
  assert.deepEqual(exportSelection([domain], [artifact]), { domain_ids: [domain], artifact_ids: [artifact] });
  for (const [domains, artifacts] of [[[], []], [[domain, domain], []], [[], [artifact, artifact]], [["../domain"], []], [[], ["bad"]]]) {
    assert.throws(() => exportSelection(domains, artifacts));
  }
});
