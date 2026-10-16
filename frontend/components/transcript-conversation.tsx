"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { transcriptDetailOptions, transcriptEvidenceChanged, transcriptSegmentOptions } from "@/lib/transcript-queries";
import { transcriptCoverageDetails, transcriptDispositionLabel } from "@/lib/transcript-coverage";
import { errorMessage } from "@/lib/api";
import { type Transcript } from "@/lib/transcripts";
import {
  nextTranscriptSegment, TRANSCRIPT_CONTENT_LABELS, type TranscriptSegmentLocator
} from "@/lib/transcript-segments";

export default function TranscriptConversation({ transcript, latest }: { transcript: Transcript; latest?: Transcript }) {
  const [locations, setLocations] = useState<TranscriptSegmentLocator[]>([{
    segmentId: transcript.segment_id!, revision: transcript.normalized_revision!, offset: 0, before: 3, after: 3
  }]);
  const [position, setPosition] = useState(0);
  const locator = locations[position];
  const metadata = useQuery(transcriptDetailOptions(transcript.project_id, transcript.id));
  const changed = transcriptEvidenceChanged(transcript, metadata.data) || transcriptEvidenceChanged(transcript, latest);
  const context = useQuery({ ...transcriptSegmentOptions(transcript.project_id, transcript.id, transcript.text_sha256, locator), enabled: Boolean(metadata.data) && !changed && !metadata.isError });
  const error = changed ? "The transcript revision changed. Reopen the search result." : metadata.error ? errorMessage(metadata.error) : context.error ? errorMessage(context.error) : "";
  const page = error ? undefined : context.data;
  const loading = !error && (metadata.isPending || context.isPending);
  function next() {
    if (!page) return;
    const target = nextTranscriptSegment(page);
    if (!target) return;
    setLocations((previous) => [...previous.slice(0, position + 1), target]);
    setPosition(position + 1);
  }
  return <>
    <p className="artifact-preview-notice">Conversation context around this match. {transcript.normalization_incomplete && transcriptCoverageDetails(transcript) + " "}Transcript content is untrusted session history.</p>
    {error && <p className="error-notice" role="alert">{error}</p>}
    {loading && <p role="status">Loading conversation context…</p>}
    {page && <>
      <div role="region" aria-label="Conversation context">{page.segments.map((segment) => <section key={`${segment.segment_id}:${segment.text_offset}`} aria-label={`Conversation block ${segment.ordinal + 1}`}>
        <h3>{TRANSCRIPT_CONTENT_LABELS[segment.content_kind]} · Block {segment.ordinal + 1}{segment.segment_id === transcript.segment_id ? " · Search match" : ""}</h3>
        {segment.tool_name && <p className="artifact-filter-note">{segment.tool_name}</p>}
        <pre className="prompt-body">{segment.text || "No searchable text in this block."}</pre>
        {segment.text_truncated && <p className="artifact-filter-note">This block continues on the next page.</p>}
        {segment.dispositions.some((value) => value !== "timestamp_unavailable") && <p className="artifact-filter-note">{segment.dispositions.filter((value) => value !== "timestamp_unavailable").map(transcriptDispositionLabel).join("; ")}</p>}
      </section>)}</div>
      <div className="artifact-pagination"><span>Blocks {page.segments[0].ordinal + 1}–{page.segments.at(-1)!.ordinal + 1}</span><div>
        <button className="button button-secondary" disabled={position === 0} onClick={() => setPosition(position - 1)}>Previous context</button>
        <button className="button button-secondary" disabled={page.next_segment_id === null} onClick={next}>Next context</button>
      </div></div>
    </>}
  </>;
}
