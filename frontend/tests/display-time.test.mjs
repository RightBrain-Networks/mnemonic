import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  formatDateTime,
  getDisplayTimeZone,
  setDisplayTimeZone
} from "../lib/display-time.ts";

test("dashboard timestamps use the configured Eastern timezone", () => {
  setDisplayTimeZone("America/Detroit");

  assert.equal(getDisplayTimeZone(), "America/Detroit");
  assert.equal(formatDateTime("2026-09-01T16:00:00Z"), "Sep 1, 2026 12:00 pm");
});

test("invalid timezone settings safely fall back to UTC", () => {
  setDisplayTimeZone("not/a-timezone");

  assert.equal(getDisplayTimeZone(), "UTC");
  assert.equal(formatDateTime("2026-09-01T16:00:00Z"), "Sep 1, 2026 4:00 pm");
});
