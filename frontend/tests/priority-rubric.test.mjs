import assert from "node:assert/strict";
import test from "node:test";
import { decodePriorityRubric, validPriorityRubricContent, PRIORITY_RUBRIC_RESPONSE_BYTES } from "../lib/priority-rubric.ts";

const projectId = "ca501b3f-860b-4f88-bca8-2f22a06359ab";
const route = `projects/${projectId}/priority-rubric`;
const rubric = { project_id: projectId, content: "  # Priority\r\n\n**Impact** 📄\n", revision: "2" };

test("rubric decoding preserves exact Markdown and rejects wrong project or revision", () => {
  assert.deepEqual(decodePriorityRubric(rubric, projectId), rubric);
  for (const changes of [{ project_id: crypto.randomUUID() }, { revision: "0" }, { revision: 2 },
    { revision: "9223372036854775808" }, { extra: true }, { content: "" }, { content: "\ud800" }]) {
    assert.throws(() => decodePriorityRubric({ ...rubric, ...changes }, projectId));
  }
});

test("rubric text uses Unicode character limits and preserves whitespace", () => {
  assert.equal(validPriorityRubricContent("📄".repeat(100000)), true);
  for (const text of ["", " \n\t", "a\0b", "\ud800", "📄".repeat(100001)]) {
    assert.equal(validPriorityRubricContent(text), false);
  }
});
