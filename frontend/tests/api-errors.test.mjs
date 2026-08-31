import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, detailMessage, isVersionConflict } from "../lib/api.ts";

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

test("only typed or legacy code-less version conflicts use stale-version recovery", () => {
  assert.equal(isVersionConflict(new ApiError("changed", 409, "version_conflict")), true);
  assert.equal(isVersionConflict(new ApiError("legacy conflict", 409)), true);
  assert.equal(isVersionConflict(new ApiError("held", 409, "lease_held")), false);
  assert.equal(isVersionConflict(new ApiError("expired", 409, "lease_expired")), false);
  assert.equal(isVersionConflict(new ApiError("wrong status", 422, "version_conflict")), true);
  assert.equal(isVersionConflict(new Error("not an API error")), false);
});
