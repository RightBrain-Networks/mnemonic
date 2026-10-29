import assert from "node:assert/strict";
import test from "node:test";
import { exportSelection } from "../lib/project-export.ts";

const domain = "11111111-1111-4111-8111-111111111111";
test("exports support selected domains and one all-artifacts choice independently", () => {
  assert.deepEqual(exportSelection([domain], false), { domain_ids: [domain], include_artifacts: false });
  assert.deepEqual(exportSelection([], true), { domain_ids: [], include_artifacts: true });
  assert.deepEqual(exportSelection([domain], true), { domain_ids: [domain], include_artifacts: true });
  for (const [domains, includeArtifacts] of [[[], false], [[domain, domain], false], [["../domain"], true]]) {
    assert.throws(() => exportSelection(domains, includeArtifacts));
  }
});
