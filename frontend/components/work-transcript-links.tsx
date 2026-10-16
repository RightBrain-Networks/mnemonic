import Link from "@/components/dashboard-link";
import { transcriptLibraryPath } from "@/lib/transcripts";
import type { WorkTranscriptLinks as Links } from "@/lib/work-transcripts";

export default function WorkTranscriptLinks({ projectId, workItemId, links }: { projectId: string; workItemId: string; links?: Links }) {
  return <section className="work-transcript-links" aria-label="Linked transcripts">
    <Link className="text-button" href={transcriptLibraryPath(projectId, workItemId)}>Transcripts{links ? ` (${links.total})` : ""}</Link>
    {links && links.items.length > 0 && <ul>{links.items.slice(0, 3).map((item) => <li key={item.id}><Link href={`${transcriptLibraryPath(projectId, workItemId)}&transcript=${item.id}`}>{item.filename}</Link>{item.models.length > 0 && <small> · {item.models.join(", ")}</small>}</li>)}</ul>}
  </section>;
}
