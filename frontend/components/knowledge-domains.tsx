"use client";

import { Fragment, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import DomainDialog from "@/components/knowledge-domain-dialog";
import DomainDecisionReviewSettings from "@/components/domain-decision-review-settings";
import KnowledgeDeleteDialog from "@/components/knowledge-delete-dialog";
import { KnowledgeReadError } from "@/components/knowledge-shared";
import { formatDateTime } from "@/lib/display-time";
import { knowledgeNextOffset, type KnowledgeDomain } from "@/lib/knowledge";
import { knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";

export default function KnowledgeDomains({ projectId, onPendingChange }: { projectId: string; onPendingChange: (pending: boolean) => void }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState<KnowledgeDomain | "new" | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeDomain | null>(null);
  const [pendingDomain, setPendingDomain] = useState<KnowledgeDomain | null>(null);
  const [dialogPending, setDialogPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const domains = useInfiniteQuery({ queryKey: knowledgeKeys.domains(projectId), initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readDomainPage(projectId, pageParam, signal),
    getNextPageParam: (last) => knowledgeNextOffset(last), retry: false, refetchInterval: 30_000, refetchIntervalInBackground: false });
  const items = domains.data?.pages.flatMap((page) => page.items) ?? [];
  const rows = pendingDomain && !items.some((domain) => domain.id === pendingDomain.id) ? [...items, pendingDomain] : items;
  const blocked = Boolean(pendingDomain) || dialogPending;
  const refresh = () => void client.invalidateQueries({ queryKey: knowledgeKeys.root(projectId) });
  const changeDialogPending = (pending: boolean) => { setDialogPending(pending); onPendingChange(pending); };
  return <section className="knowledge-domains" aria-label="Manage knowledge domains">
    <div className="artifact-directory-heading"><div><h2>Knowledge domains<span className="artifact-count">{domains.data?.pages[0]?.total ?? ""}</span></h2><p className="field-hint">Organize decision documents and choose how changes are reviewed in each domain.</p></div><button className="button button-primary" disabled={blocked} onClick={() => setEditing("new")}>Add domain</button></div>
    {notice && <p className="artifact-notice" role="status">{notice}</p>}
    {domains.isError && <KnowledgeReadError error={domains.error} retry={() => void domains.refetch()} />}
    {domains.isPending && <p role="status">Loading domains…</p>}
    <div className="artifact-table-scroll knowledge-domain-directory" role="region" aria-label="Knowledge domain directory" tabIndex={0}>
      <table className="artifact-table knowledge-domain-table"><thead><tr><th scope="col">Domain</th><th scope="col">Documents</th><th scope="col">Automatic reviews</th><th scope="col">Modified</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{rows.map((domain) => <Fragment key={domain.id}>
          <tr aria-label={`${domain.name} domain`}>
            <td><strong className="knowledge-domain-name">{domain.name}</strong><span className="artifact-type">{domain.prefix} · Permanent prefix{domain.is_product_decision_domain ? " · Product Decision Records" : ""}</span></td>
            <td>{domain.decision_count}</td>
            <td>{domain.automatic_decision_reviews === "on" ? "On" : "Off"} · Priority {domain.decision_review_priority}</td>
            <td><time dateTime={domain.updated_at}>{formatDateTime(domain.updated_at)}</time></td>
            <td><div className="artifact-actions knowledge-domain-actions">
              <button type="button" className="button button-secondary" disabled={blocked} aria-expanded={expanded === domain.id} aria-controls={`domain-settings-${domain.id}`} onClick={() => setExpanded(expanded === domain.id ? null : domain.id)}>Review settings</button>
              <button type="button" className="button button-secondary" disabled={blocked} onClick={() => setEditing(domain)}>Rename</button>
              <button type="button" className="button button-danger" disabled={blocked || domain.is_product_decision_domain} title={domain.is_product_decision_domain ? "Product Decision Records preserve human question and answer history." : undefined} onClick={() => setDeleting(domain)}>Delete</button>
            </div></td>
          </tr>
          {expanded === domain.id && <tr className="knowledge-domain-settings-row"><td colSpan={5}><div id={`domain-settings-${domain.id}`} className="knowledge-domain-settings">
            {domain.is_product_decision_domain && <p className="field-hint">Product Decision Records retain Needs Attention questions and answers. This built-in domain cannot be deleted.</p>}
            <DomainDecisionReviewSettings domain={domain} unavailable={domains.isError} refreshing={domains.isFetching} blocked={dialogPending || pendingDomain !== null && pendingDomain.id !== domain.id}
              onPendingChange={(pending) => { setPendingDomain(pending ? domain : null); onPendingChange(pending); }}
              onReload={() => void domains.refetch()} onSaved={(saved) => { setNotice(`Automatic decision impact review settings for “${saved.name}” saved.`); refresh(); }} />
          </div></td></tr>}
        </Fragment>)}</tbody>
      </table>
    </div>
    {domains.hasNextPage && <button className="button button-secondary" disabled={domains.isFetchingNextPage} onClick={() => void domains.fetchNextPage()}>More domains</button>}
    {editing && <DomainDialog projectId={projectId} domain={editing === "new" ? undefined : editing} onPendingChange={changeDialogPending} onClose={() => setEditing(null)} onSaved={(saved) => { setEditing(null); setNotice(`Domain “${saved.name}” saved.`); refresh(); }} />}
    {deleting && <KnowledgeDeleteDialog projectId={projectId} target={{ kind: "domain", id: deleting.id, name: deleting.name }} onPendingChange={changeDialogPending} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); setNotice("Domain deleted. Its deletion record remains available to agents."); refresh(); }} />}
  </section>;
}
