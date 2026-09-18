import { transcriptLibraryPath } from "@/lib/transcripts";
import type { WorkTranscriptLinks as Links } from "@/lib/work-transcripts";

export default function WorkTranscriptLinks({ projectId, workItemId, links }: { projectId: string; workItemId: string; links?: Links }) {
  return <section className="work-transcript-links" aria-label="Linked transcripts">
    <a className="text-button" href={transcriptLibraryPath(projectId, workItemId)}>Transcripts{links ? ` (${links.total})` : ""}</a>
    {links && links.items.length > 0 && <ul>{links.items.slice(0, 3).map((item) => <li key={item.id}><a href={`${transcriptLibraryPath(projectId, workItemId)}&transcript=${item.id}`}>{item.filename}</a>{item.models.length > 0 && <small> · {item.models.join(", ")}</small>}</li>)}</ul>}
  </section>;
}
