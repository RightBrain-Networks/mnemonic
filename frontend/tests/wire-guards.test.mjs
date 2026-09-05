import { decodeWorkItem } from "../lib/work-codecs.ts";
import assert from "node:assert/strict";
import test from "node:test";
import {
  UUID_PATTERN,
  UTC_DATE_TIME_PATTERN,
  boundedText,
  exactKeys,
  finiteInteger,
  nullableBoundedText,
  nullableUuid,
  objectValue,
  sameUuid,
  validBoundedMetadata,
  validUnicode,
  validUtcDateTime,
  validUuid
} from "../lib/wire-guards.ts";

const project = "e36a7e53-938f-4c8a-b75a-af9c7331711a";
const work = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const checkpoint = "1dfa9455-4a17-4cd4-938b-010ea17ccaf0";

function workItem(overrides = {}) {
  return {
    id: work,
    project_id: project,
    title: "Durable objective",
    summary: "Keep the exact context",
    status: "pending",
    priority: 5,
    initial_checkpoint_id: checkpoint,
    version: 2,
    created_at: "2026-09-01T12:00:00Z",
    updated_at: "2026-09-01T12:01:00.123Z",
    ...overrides
  };
}

test("plain-object, exact-key, and finite-integer guards reject widened wire values", () => {
  const nullPrototype = Object.assign(Object.create(null), { value: 1 });
  assert.equal(objectValue(null), null);
  assert.equal(objectValue([]), null);
  assert.equal(objectValue(new Date()), null);
  assert.equal(objectValue({ value: 1 }).value, 1);
  assert.equal(objectValue(nullPrototype), nullPrototype);

  assert.equal(exactKeys({ first: 1, second: 2 }, ["second", "first"]), true);
  assert.equal(exactKeys({ first: 1 }, new Set(["first"])), true);
  assert.equal(exactKeys({ first: 1, extra: 2 }, ["first"]), false);
  assert.equal(exactKeys({ first: 1 }, ["first", "missing"]), false);

  assert.equal(finiteInteger(0), true);
  assert.equal(finiteInteger(Number.MAX_SAFE_INTEGER), true);
  assert.equal(finiteInteger(4, 1, 4), true);
  assert.equal(finiteInteger(0, 1), false);
  assert.equal(finiteInteger(5, 1, 4), false);
  assert.equal(finiteInteger(1.5), false);
  assert.equal(finiteInteger(Number.MAX_SAFE_INTEGER + 1), false);
  assert.equal(finiteInteger(Number.NaN), false);
});

test("Unicode and bounded-text guards count code points and reject unsafe strings", () => {
  assert.equal(validUnicode(""), true);
  assert.equal(validUnicode("A😀é"), true);
  assert.equal(validUnicode("\ud800"), false);
  assert.equal(validUnicode("\udc00"), false);
  assert.equal(validUnicode("\ud800x"), false);

  assert.equal(boundedText("😀", 1), true);
  assert.equal(boundedText("😀", 0), false);
  assert.equal(boundedText("e\u0301", 1), false);
  assert.equal(boundedText(" useful ", 8), true);
  assert.equal(boundedText(" \r\n\t", 20), false);
  assert.equal(boundedText("nul\0byte", 20), false);
  assert.equal(boundedText("\ud800", 20), false);
  assert.equal(boundedText(17, 20), false);
  assert.equal(nullableBoundedText(null, 1), true);
  assert.equal(nullableBoundedText(undefined, 1), false);
});

test("UUID guards are anchored, case-insensitive, and preserve wire-level version breadth", () => {
  const uppercase = project.toUpperCase();
  assert.equal(validUuid(project), true);
  assert.equal(validUuid(uppercase), true);
  assert.equal(UUID_PATTERN.test(project), true);
  assert.equal(validUuid("x" + project), false);
  assert.equal(validUuid(project + "x"), false);
  assert.equal(validUuid("{e36a7e53-938f-4c8a-b75a-af9c7331711a}"), false);
  assert.equal(validUuid("e36a7e53-938f-0c8a-075a-af9c7331711a"), true);
  assert.equal(nullableUuid(null), true);
  assert.equal(nullableUuid(undefined), false);
  assert.equal(sameUuid(project, uppercase), true);
  assert.equal(sameUuid(project, work), false);
  assert.equal(sameUuid(project, "not-a-uuid"), false);
});

test("UTC timestamp guards validate calendar boundaries and the canonical Z form", () => {
  for (const value of [
    "2024-02-29T23:59:59Z",
    "2000-02-29T00:00:00.123456Z",
    "2026-09-01T12:00:00Z"
  ]) {
    assert.equal(validUtcDateTime(value), true, value);
    assert.equal(UTC_DATE_TIME_PATTERN.test(value), true, value);
  }
  for (const value of [
    "0000-01-01T00:00:00Z",
    "1900-02-29T00:00:00Z",
    "2023-02-29T00:00:00Z",
    "2024-04-31T00:00:00Z",
    "2024-01-01T24:00:00Z",
    "2024-01-01T00:60:00Z",
    "2024-01-01T00:00:60Z",
    "2024-01-01T00:00:00+00:00",
    "2024-01-01T00:00:00z",
    "2024-01-01"
  ]) assert.equal(validUtcDateTime(value), false, value);
});

test("bounded metadata rejects secrets, cycles, invalid JSON values, and oversized data", () => {
  const reserved = new Set(["secret", "gate_id"]);
  const shared = { count: 2 };
  assert.equal(validBoundedMetadata({ nested: [shared, shared], label: "😀" }, reserved), true);
  assert.equal(validBoundedMetadata({}, reserved), true);
  assert.equal(validBoundedMetadata([], reserved), false);
  assert.equal(validBoundedMetadata({ nested: { SeCrEt: "value" } }, reserved), false);
  assert.equal(validBoundedMetadata({ nested: { GATE_ID: checkpoint } }, reserved), false);
  assert.equal(validBoundedMetadata({ "bad\0key": true }, reserved), false);
  assert.equal(validBoundedMetadata({ value: "bad\0value" }, reserved), false);
  assert.equal(validBoundedMetadata({ value: "\ud800" }, reserved), false);
  assert.equal(validBoundedMetadata({ value: Number.POSITIVE_INFINITY }, reserved), false);
  assert.equal(validBoundedMetadata({ value: 1n }, reserved), false);
  assert.equal(validBoundedMetadata({ value: new Date() }, reserved), false);
  assert.equal(validBoundedMetadata({ note: "x".repeat(100) }, reserved, 40), false);

  const cyclic = {};
  cyclic.self = cyclic;
  assert.equal(validBoundedMetadata(cyclic, reserved), false);
});

test("the shared work-item decoder enforces exact shape, scope, bounds, and timestamps", () => {
  const item = workItem();
  assert.equal(decodeWorkItem(item, project, work), item);
  assert.equal(decodeWorkItem(item, project.toUpperCase(), work.toUpperCase()), item);

  for (const invalid of [
    workItem({ extra: true }),
    workItem({ project_id: work }),
    workItem({ id: project }),
    workItem({ title: "   " }),
    workItem({ summary: "x".repeat(1_001) }),
    workItem({ status: "open" }),
    workItem({ priority: 101 }),
    workItem({ version: 0 }),
    workItem({ created_at: "2023-02-29T00:00:00Z" }),
    workItem({ updated_at: "2026-09-01T12:01:00+00:00" })
  ]) assert.throws(() => decodeWorkItem(invalid, project, work), /invalid mutation response/);

  assert.throws(
    () => decodeWorkItem(item, work, undefined, "Attention work item is invalid."),
    /Attention work item is invalid/
  );
});
