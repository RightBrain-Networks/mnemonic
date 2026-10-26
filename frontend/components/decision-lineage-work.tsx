"use client";

import Link from "@/components/dashboard-link";
import { KnowledgePagination } from "@/components/knowledge-shared";
import { formatDateTime } from "@/lib/display-time";
import type { LineageWorkPage } from "@/lib/decision-lineage";

export function DecisionLineageWork({ page, busy, onOffset }: {
  page: LineageWorkPage; busy: boolean; onOffset: (offset: number) => void;
}) {
  return <section aria-label="Work in this decision change">
    <h4>Work in this decision change</h4>
    <p className="field-hint">Check relevant implementation, reviews and remediation before recording conclusions. Shared lineage alone does not establish that a finding affects this assessment.</p>
    <ol className="decision-history">{page.items.map(item => <li key={item.work_item_id}>
      {item.deleted_at ? <strong>{item.title}</strong>
        : <Link href={`/work-items?project=${item.project_id}&work=${item.work_item_id}`}>{item.title}</Link>}
      <span>{item.deleted_at ? "Deleted" : item.readiness?.display_state.replaceAll("-", " ")}
        {item.is_decision_source && " · Decision source"}
        {item.remediation_origin && " · Code review remediation"}</span>
      {item.readiness?.active_lease && <span>Active with {item.readiness.active_lease.holder_client} until {formatDateTime(item.readiness.active_lease.expires_at)}</span>}
      {item.code_review && <span>Code review: {(item.code_review.status ?? item.code_review.state).replaceAll("-", " ")}</span>}
      {item.review_follow_up && <span>Review recommendation: {(item.review_follow_up.status ?? item.review_follow_up.state).replaceAll("-", " ")}</span>}
      {item.remediation_origin && <Link href={`/work-items?project=${item.remediation_origin.source_project_id}&work=${item.remediation_origin.source_work_item_id}`}>Open the reviewed implementation</Link>}
      {item.discovered_from.items.length > 0 && <details><summary>Discovery origins ({item.discovered_from.total})</summary>
        <ul>{item.discovered_from.items.map(origin => <li key={origin.work_item_id}><Link href={`/work-items?project=${origin.project_id}&work=${origin.work_item_id}`}>Work {origin.work_item_id}</Link></li>)}</ul>
        {item.discovered_from.omitted_count > 0 && <p>{item.discovered_from.omitted_count} more origins. Open the work item for its full relationships.</p>}
      </details>}
    </li>)}</ol>
    {!page.items.length && <p>No work on this page.</p>}
    <KnowledgePagination page={page} noun="work items" busy={busy} onOffset={onOffset} />
  </section>;
}
