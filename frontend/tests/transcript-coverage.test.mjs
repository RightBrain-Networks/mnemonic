import assert from "node:assert/strict";
import test from "node:test";
import { transcriptCoverageLabel, transcriptCoverageDetails, transcriptCoverageNotes, transcriptDispositionLabel } from "../lib/transcript-coverage.ts";

const transcript = (warnings, incomplete = true) => ({ normalization_incomplete: incomplete, metadata: { "transcript:normalization_warnings": warnings } });

test("remaining media limitations identify the content and affected block count", () => {
  const row = transcript(["image_content_not_searchable=3", "document_content_not_searchable=1"]);
  assert.equal(transcriptCoverageLabel(row), "Images not searchable; Documents not searchable");
  assert.match(transcriptCoverageDetails(row), /Images not searchable \(3 blocks\)/);
  assert.match(transcriptCoverageDetails(row), /Documents not searchable \(1 block\)/);
  assert.match(transcriptCoverageDetails(row), /native copy retains the original records/);
});

test("encrypted state is a disclosed native limitation without a parser failure badge", () => {
  const row = { ...transcript([], false), metadata: { "transcript:normalization_notes": ["encrypted_content_omitted=5", "compaction_context=2"] } };
  assert.equal(transcriptCoverageLabel(row), "");
  assert.match(transcriptCoverageNotes(row), /Provider-encrypted content has no readable text/);
  assert.match(transcriptCoverageNotes(row), /Readable context recovered from compaction/);
  assert.equal(transcriptCoverageDetails(row), "All supported readable content represented");
});

test("unknown codes and historical manifests keep an honest coverage fallback", () => {
  for (const warnings of [undefined, [], ["future_warning=2"], ["unsupported_content=-1"], ["<script>=1"]]) {
    assert.equal(transcriptCoverageLabel(transcript(warnings)), "Coverage incomplete");
    assert.doesNotMatch(transcriptCoverageDetails(transcript(warnings)), /<script>|-1/);
  }
  assert.equal(transcriptDispositionLabel("call_reference_unresolved"), "Tool call missing from capture");
  assert.equal(transcriptDispositionLabel("unsupported_record"), "Unrecognized native records");
});
