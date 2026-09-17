import type { Transcript } from "./transcripts.ts";

const labels: Readonly<Record<string, string>> = {
  image_content_not_searchable: "Images not searchable",
  document_content_not_searchable: "Documents not searchable",
  audio_content_not_searchable: "Audio not searchable",
  video_content_not_searchable: "Video not searchable",
  attachment_content_not_searchable: "Binary attachments not searchable",
  unsupported_content: "Unrecognized native content",
  unsupported_record: "Unrecognized native records",
  unsupported_role: "Unrecognized native message type",
  unsupported_attachment: "Unrecognized native attachment",
  call_reference_unresolved: "Tool call missing from capture",
  content_nesting_limit: "Content nesting limit reached",
  replacement_history_omitted: "Compaction history not represented"
};
const explanations: Readonly<Record<string, string>> = {
  encrypted_content_omitted: "Provider-encrypted content has no readable text; the native copy retains it",
  compaction_context: "Readable context recovered from compaction history",
  compaction_replay_deduplicated: "Repeated compaction context already represented",
  bookkeeping_or_mirrored_event: "Client bookkeeping and mirrored events excluded from conversation text",
  timestamp_unavailable: "Native timestamp unavailable",
  payload_omitted_for_budget: "Structured payload exceeds this response's size limit",
  metadata_omitted_for_budget: "Native metadata exceeds this response's size limit"
};

function counts(values: string[] = []): { code: string; count: number }[] {
  return values.flatMap((value) => {
    const match = /^([a-z_]+)=([1-9][0-9]*)$/.exec(value);
    if (!match || !Number.isSafeInteger(Number(match[2]))) return [];
    return [{ code: match[1], count: Number(match[2]) }];
  });
}
export function transcriptDispositionLabel(code: string): string {
  return labels[code] || explanations[code] || "Native content could not be fully represented";
}
export function transcriptCoverageLabel(transcript: Transcript): string {
  if (!transcript.normalization_incomplete) return "";
  const warnings = counts(transcript.metadata["transcript:normalization_warnings"]);
  return [...new Set(warnings.map(({ code }) => labels[code] || "Coverage incomplete"))].join("; ") || "Coverage incomplete";
}
export function transcriptCoverageDetails(transcript: Transcript): string {
  if (!transcript.normalization_incomplete) return "All supported readable content represented";
  const warnings = counts(transcript.metadata["transcript:normalization_warnings"]);
  const detail = warnings.map(({ code, count }) => `${transcriptDispositionLabel(code)} (${count} ${count === 1 ? "block" : "blocks"})`).join("; ");
  return (detail || "Some native content or tool links could not be represented") + ". The native copy retains the original records.";
}
export function transcriptCoverageNotes(transcript: Transcript): string {
  return counts(transcript.metadata["transcript:normalization_notes"])
    .filter(({ code }) => code !== "timestamp_unavailable")
    .map(({ code, count }) => `${transcriptDispositionLabel(code)} (${count})`).join("; ") || "None";
}
