import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { decodeTranscriptSegments, nextTranscriptSegment, transcriptSegmentQuery, validContentKinds } from "../lib/transcript-segments.ts";

const project = "7a5dc555-0a6d-4f92-9678-1647524827c8";
const transcript = "4b60cbb5-908e-4ea3-b03e-c448bc469339";
const revision = "a".repeat(64);
const hash = (value) => createHash("sha256").update(value).digest("hex").slice(0, 24);
function segment(ordinal, patch = {}) {
  const event = hash(`${revision}:${ordinal + 1}`);
  return { segment_id: hash(`${event}:0`), event_id: event, ordinal, source_record: ordinal + 1,
    source_block: "0", role: "assistant", content_kind: "assistant_text", text: "Hello 🌲",
    timestamp: null, native_event_id: null, native_parent_id: null, native_branch_id: null, channel: null,
    is_sidechain: null, is_error: null, tool_name: null, call_id: null, payload: null, dispositions: [],
    related_segment_id: null, text_offset: 0, text_truncated: false, ...patch };
}
const locator = { segmentId: segment(0).segment_id, revision, offset: 0, before: 0, after: 1 };
function page(patch = {}) {
  const segments = [segment(0), segment(1)];
  const text = segments.map((row) => row.text).join("\n\n");
  return { project_id: project, transcript_id: transcript, text_sha256: null, normalized_revision: revision,
    text, total_chars: Array.from(text).length, offset: 0, limit: 20000, next_offset: null,
    status: "pending", truncated: false, segments, segment_window: { anchor_segment_id: locator.segmentId,
      anchor_ordinal: 0, first_ordinal: 0, last_ordinal: 1 }, next_segment_id: null,
    next_segment_offset: null, next_segment_after: null, ...patch };
}

test("content kinds use semantic categories independently of native message role", () => {
  assert.equal(validContentKinds(["human_text", "tool_result"]), true);
  for (const value of [[], ["user"], ["tool_result", "tool_result"], [true], null]) assert.equal(validContentKinds(value), false);
});

test("structured context can precede indexing and validates deterministic revision identities", async () => {
  const actual = await decodeTranscriptSegments(page(), project, transcript, locator);
  assert.equal(actual.status, "pending");
  assert.equal(nextTranscriptSegment(actual), null);
  for (const patch of [{ normalized_revision: "b".repeat(64) }, { project_id: transcript }, { text: "forged" },
    { segments: [segment(0, { source_record: 3 }), segment(1)] }, { segments: [segment(1), segment(0)] },
    { segments: [segment(0, { role: "a".repeat(4097) }), segment(1)] }, { next_segment_offset: 0 }]) {
    await assert.rejects(decodeTranscriptSegments(page(patch), project, transcript, locator));
  }
});

test("a large preceding block preserves the original window through continuation", async () => {
  const first = segment(0, { text_truncated: true });
  const anchor = segment(1);
  const request = { ...locator, segmentId: anchor.segment_id, before: 1 };
  const response = page({ text: first.text, total_chars: 30000, segments: [first],
    segment_window: { anchor_segment_id: anchor.segment_id, anchor_ordinal: 1, first_ordinal: 0, last_ordinal: 2 },
    next_segment_id: first.segment_id, next_segment_offset: Array.from(first.text).length, next_segment_after: 2 });
  const actual = await decodeTranscriptSegments(response, project, transcript, request);
  const next = nextTranscriptSegment(actual);
  assert.deepEqual(next, { segmentId: first.segment_id, revision, offset: 7, before: 0, after: 2 });
  assert.equal(transcriptSegmentQuery(next).get("expected_normalized_revision"), revision);
  await assert.rejects(decodeTranscriptSegments({ ...response, next_segment_offset: 0 }, project, transcript, request));
  await assert.rejects(decodeTranscriptSegments({ ...response, next_segment_after: 1 }, project, transcript, request));
});

test("locator requests preserve flat text hashes and permit large canonical offsets", () => {
  const query = transcriptSegmentQuery({ ...locator, offset: 8_000_001 }, "b".repeat(64));
  assert.equal(query.get("expected_sha256"), "b".repeat(64));
  assert.equal(query.get("offset"), "8000001");
  for (const patch of [{ before: 1, offset: 1 }, { before: 20, after: 1 }, { revision: "bad" }]) {
    assert.throws(() => transcriptSegmentQuery({ ...locator, ...patch }));
  }
});


test("optional metadata and opaque tool payloads remain bounded and agree with omissions", async () => {
  for (const segments of [[segment(0, { payload: "a".repeat(20000) }), segment(1)],
    [segment(0, { role: "a".repeat(3000) }), segment(1, { role: "a".repeat(3000) })],
    [segment(0, { dispositions: ["metadata_omitted_for_budget"] }), segment(1)],
    [segment(0, { payload: {}, dispositions: ["payload_omitted_for_budget"] }), segment(1)]]) {
    await assert.rejects(decodeTranscriptSegments(page({ segments }), project, transcript, locator));
  }
});
