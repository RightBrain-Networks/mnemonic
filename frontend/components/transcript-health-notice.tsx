"use client";

import { useQuery } from "@tanstack/react-query";
import { transcriptHealthOptions } from "@/lib/transcript-queries";
import { formatTranscriptSize } from "@/lib/transcript-settings";
import type { TranscriptDirectoryUsage } from "@/lib/transcript-health";

function StorageItem({ label, usage, description }: { label: string; usage: TranscriptDirectoryUsage | null; description?: string }) {
  const stale = usage && Date.now() - Date.parse(usage.checked_at) > 120000;
  return <div><dt>{label}</dt><dd>{usage?.bytes != null ? `${usage.complete ? "" : "At least "}${formatTranscriptSize(usage.bytes)}` : "Unavailable"}</dd>
    {description && <small>{description}</small>}
    <small>{usage?.free_bytes != null ? `${formatTranscriptSize(usage.free_bytes)} free on volume` : "Volume space unavailable"}{stale ? " · Last reported" : ""}</small>
    {usage && !usage.complete && <small>Storage measurement incomplete</small>}
  </div>;
}

export default function TranscriptHealthNotice({ projectId }: { projectId: string }) {
  const result = useQuery(transcriptHealthOptions(projectId));
  const health = result.data;
  return <>
    {health?.storage && <section className="transcript-storage" aria-label="Transcript storage usage"><div className="transcript-storage-heading"><strong>Storage</strong><span>All projects · disk space used</span></div><dl>
      <StorageItem label="Retained transcripts" usage={health.storage.transcripts} />
      <StorageItem label="Search cache" usage={health.storage.index} description="Built on demand for the latest search." />
      <div><dt>Conversation database</dt><dd>{formatTranscriptSize(health.storage.database_bytes)}</dd><small>Text, metadata and database indexes</small></div>
    </dl></section>}
    {result.isError && <p className="artifact-filter-note" role="status">Storage and access status could not be checked. Refresh to retry.</p>}
  </>;
}
