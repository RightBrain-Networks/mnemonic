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
  assert.deepEqual(detailMessage([
    { loc: ["body", "actor", "actor_session_id"], msg: "Field required" },
    { loc: ["query", "limit"], msg: "Input should be less than or equal to 100" },
    { loc: ["body", "body"], msg: "String should have at least 1 character" },
    { loc: ["body", "tags", 2], msg: "Input should be a valid string" }
  ]), {
    message: "actor.actor_session_id: Field required. limit: Input should be less than or equal to 100. body: String should have at least 1 character. tags.2: Input should be a valid string"
  });
});

test("affected-path validation identifies only the safe field and entry index", () => {
  const secretPath = "secret/path/that-must-not-render";
  const detail = detailMessage([{
    loc: ["body", "checkpoint", "affected_paths", 7],
    msg: "Value does not match the affected-path grammar",
    input: secretPath
  }]);
  assert.equal(
    detail.message,
    "checkpoint.affected_paths.7: Value does not match the affected-path grammar"
  );
  assert.equal(detail.message.includes(secretPath), false);
});

test("validation locations never render attacker-controlled extra field names", () => {
  const sensitive = "SENSITIVE_CALLER_KEY_123";
  const detail = detailMessage([
    { loc: ["body", sensitive], msg: "Extra inputs are not permitted" },
    { loc: ["body", "metadata", sensitive], msg: "Extra inputs are not permitted" },
    { loc: { attacker: sensitive }, msg: "Invalid value" }
  ]);

  assert.equal(detail.message, "Extra inputs are not permitted. metadata: Extra inputs are not permitted. Invalid value");
  assert.equal(detail.message.includes(sensitive), false);
});

test("only typed or legacy code-less version conflicts use stale-version recovery", () => {
  assert.equal(isVersionConflict(new ApiError("changed", 409, "version_conflict")), true);
  assert.equal(isVersionConflict(new ApiError("legacy conflict", 409)), true);
  assert.equal(isVersionConflict(new ApiError("held", 409, "lease_held")), false);
  assert.equal(isVersionConflict(new ApiError("expired", 409, "lease_expired")), false);
  assert.equal(isVersionConflict(new ApiError("wrong status", 422, "version_conflict")), true);
  assert.equal(isVersionConflict(new Error("not an API error")), false);
});
