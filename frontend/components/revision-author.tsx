import Link from "@/components/dashboard-link";
import { formatDateTime } from "@/lib/display-time";
import type { RevisionAuthorContext } from "@/lib/revision-authors";

export function RevisionAuthor({ author, projectId, label }: { author: RevisionAuthorContext; projectId: string; label: string }) {
  return <details className="revision-author">
    <summary>{label}: {author.author_kind === "human" ? "Human" : author.model_id ?? "Agent"} · {author.reference}</summary>
    <p>Authored {formatDateTime(author.authored_at)}{author.agent_session_id && <> · Session <code className="break-all">{author.agent_session_id}</code></>}</p>
    {author.author_kind === "agent" && <>
      <p>Active work held by this session, observed {formatDateTime(author.work_observed_at)}:</p>
      {!author.active_work_items.length ? <p>No active work leases in this project.</p> : <ul>{author.active_work_items.map((work) => <li key={work.work_item_id}>
        <Link href={`/work-items?project=${projectId}&work=${work.work_item_id}`}>{work.title}</Link> · {work.purpose === "code_review" ? "Code review" : "Implementation"} · {work.holder_client} · expires {formatDateTime(work.expires_at)}
      </li>)}</ul>}
      {author.active_work_items_truncated && <p className="field-hint">Additional active work is omitted.</p>}
      <p className="field-hint">Session holdings provide context. Commit provenance establishes which code used the decision.</p>
    </>}
  </details>;
}
