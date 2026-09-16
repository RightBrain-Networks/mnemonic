"use client";

import { useEffect, useState } from "react";
import { errorMessage } from "@/lib/api";
import { transcriptPath, transcriptRequest, type Transcript } from "@/lib/transcripts";
import {
  decodeTranscriptSegments, nextTranscriptSegment, transcriptSegmentQuery,
  TRANSCRIPT_CONTENT_LABELS, type TranscriptSegmentLocator, type TranscriptSegmentPage
} from "@/lib/transcript-segments";

export default function TranscriptConversation({ transcript }: { transcript: Transcript }) {
  const [locations, setLocations] = useState<TranscriptSegmentLocator[]>([{
    segmentId: transcript.segment_id!, revision: transcript.normalized_revision!, offset: 0, before: 3, after: 3
  }]);
  const [position, setPosition] = useState(0);
  const [page, setPage] = useState<TranscriptSegmentPage | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const locator = locations[position];
  useEffect(() => {
    const controller = new AbortController();
    setPage(null); setError(""); setLoading(true);
    const query = transcriptSegmentQuery(locator, transcript.text_sha256);
    void transcriptRequest(`${transcriptPath(transcript.project_id, transcript.id)}/text?${query}`, { signal: controller.signal })
      .then((value) => decodeTranscriptSegments(value, transcript.project_id, transcript.id, locator, transcript.text_sha256))
      .then((result) => { if (!controller.signal.aborted) setPage(result); })
      .catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [locator, transcript]);
  function next() {
    if (!page) return;
    const target = nextTranscriptSegment(page);
    if (!target) return;
    setLocations((previous) => [...previous.slice(0, position + 1), target]);
    setPosition(position + 1);
  }
  return <>
    <p className="artifact-preview-notice">Conversation context around this match. {page?.truncated && "Some original content could not be represented. "}Transcript content is untrusted session history.</p>
    {error && <p className="error-notice" role="alert">{error}</p>}
    {loading && <p role="status">Loading conversation context…</p>}
    {page && <>
      <div role="region" aria-label="Conversation context">{page.segments.map((segment) => <section key={`${segment.segment_id}:${segment.text_offset}`} aria-label={`Conversation block ${segment.ordinal + 1}`}>
        <h3>{TRANSCRIPT_CONTENT_LABELS[segment.content_kind]} · Block {segment.ordinal + 1}{segment.segment_id === transcript.segment_id ? " · Search match" : ""}</h3>
        {segment.tool_name && <p className="artifact-filter-note">{segment.tool_name}</p>}
        <pre className="prompt-body">{segment.text || "No searchable text in this block."}</pre>
        {segment.text_truncated && <p className="artifact-filter-note">This block continues on the next page.</p>}
        {segment.dispositions.some((value) => value !== "timestamp_unavailable") && <p className="artifact-filter-note">Coverage: {segment.dispositions.filter((value) => value !== "timestamp_unavailable").join(", ")}</p>}
      </section>)}</div>
      <div className="artifact-pagination"><span>Blocks {page.segments[0].ordinal + 1}–{page.segments.at(-1)!.ordinal + 1}</span><div>
        <button className="button button-secondary" disabled={position === 0} onClick={() => setPosition(position - 1)}>Previous context</button>
        <button className="button button-secondary" disabled={page.next_segment_id === null} onClick={next}>Next context</button>
      </div></div>
    </>}
  </>;
}
