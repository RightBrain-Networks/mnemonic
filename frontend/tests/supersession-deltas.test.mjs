import assert from "node:assert/strict";
import test from "node:test";
import { decodeSupersessionDelta } from "../lib/supersession-deltas.ts";

const target = "ADR-00001-0", after = "ADR-00002-1";
const section = () => ({ found: true, section_count: 1, text: "## Supersedes\nPrior rule", truncated: false, source_characters_scanned: 100 });
const delta = () => ({ source: "read-projection", target_reference: target, before_reference: "ADR-00002-0", after_reference: after,
  kind: "continued-supersession", details_before: null, details_after: null, metadata_changed: false,
  section_before: section(), section_after: section(), unified_diff: "", truncated: false,
  section_scan_limit: 65536, section_byte_limit: 8192, diff_byte_limit: 16384,
  interpretation: "Supersedes headings are evidence aids; read exact revisions to establish rule scope." });
const decode = (value) => decodeSupersessionDelta(value, target, after, "supersession");

test("supersession comparison retains full and compact metadata without inventing missing prose", () => {
  const full = delta();
  assert.deepEqual(decode(full), full);
  const compact = delta();
  delete compact.unified_diff;
  delete compact.section_before.text;
  delete compact.section_after.text;
  assert.deepEqual(decode(compact), compact);
  const detail = { decision_id: "ADR-00001", revision: 0, extent: "partial", reason: "Only queue order" };
  full.details_before = detail;
  full.details_after = { ...detail, extent: "full", reason: "All scope" };
  full.metadata_changed = true;
  assert.deepEqual(decode(full), full);
});

test("supersession delta pins replacement ancestry and the captured target", () => {
  for (const update of [{ source: "captured" }, { before_reference: "ADR-00002-5" }, { after_reference: "ADR-00003-1" },
    { target_reference: "ADR-00003-0" }, { metadata_changed: true }, { kind: "unknown" }, { extra: "field" },
    { section_scan_limit: 1 }, { section_byte_limit: 16384 }, { diff_byte_limit: 32768 },
    { details_after: { decision_id: "ADR-00001", revision: 1, extent: "full", reason: "Wrong target pin" }, metadata_changed: true },
    { details_before: { decision_id: "ADR-00003", revision: 0, extent: "full", reason: "Wrong target document" }, metadata_changed: true }]) {
    assert.throws(() => decode({ ...delta(), ...update }));
  }
  assert.throws(() => decodeSupersessionDelta(delta(), target, after, "revision"));
});

test("UTF-8 bounds permit exact limits and reject overflow or malformed Unicode", () => {
  const full = delta();
  full.section_after.text = "🦊".repeat(2048);
  full.unified_diff = "🦊".repeat(4096);
  assert.deepEqual(decode(full), full);
  for (const text of ["🦊".repeat(2049), "\ud800", "\udfff", "a\0b"]) {
    assert.throws(() => decode({ ...full, section_after: { ...full.section_after, text } }));
  }
  for (const unified_diff of ["🦊".repeat(4097), "\ud800", "a\0b"]) {
    assert.throws(() => decode({ ...full, unified_diff }));
  }
});

test("section discovery, bounds and truncation stay explicit", () => {
  for (const update of [{ found: false }, { section_count: 0 }, { source_characters_scanned: 65537 },
    { source_characters_scanned: -1 }, { truncated: true }, { unknown: true }]) {
    assert.throws(() => decode({ ...delta(), section_after: { ...section(), ...update } }));
  }
  const clipped = delta();
  clipped.section_after.truncated = clipped.truncated = true;
  assert.deepEqual(decode(clipped), clipped);
  const missing = delta();
  missing.section_after = { ...section(), found: false, section_count: 0, text: "" };
  assert.deepEqual(decode(missing), missing);
});
