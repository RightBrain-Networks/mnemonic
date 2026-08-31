import assert from "node:assert/strict";
import test from "node:test";
import { detailMessage } from "../lib/api.ts";

test("typed API errors retain the stable code and do not render arbitrary context", () => {
  const detail = detailMessage({
    code: "version_conflict",
    message: "The work item changed.",
    context: { expected_version: "1", actual_version: "2", internal_secret: "do-not-render" }
  });
  assert.deepEqual(detail, { code: "version_conflict", message: "The work item changed." });
});

test("validation issues remain useful without exposing raw payloads", () => {
  assert.deepEqual(detailMessage([{ loc: ["body", "title"], msg: "Field required", input: "secret" }]), { message: "title: Field required" });
});
