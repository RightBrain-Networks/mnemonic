"use client";

import { useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import DomainDialog from "@/components/knowledge-domain-dialog";
import DomainDecisionReviewSettings from "@/components/domain-decision-review-settings";
import KnowledgeDeleteDialog from "@/components/knowledge-delete-dialog";
import { KnowledgeReadError } from "@/components/knowledge-shared";
import { errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/display-time";
import { knowledgeNextOffset, knowledgePath, type KnowledgeDomain } from "@/lib/knowledge";
import { downloadKnowledgeFile } from "@/lib/knowledge-management";
import { knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";

export default function KnowledgeDomains({ projectId, onPendingChange }: { projectId: string; onPendingChange: (pending: boolean) => void }) {
  const client = useQueryClient();
  const [editing, setEditing] = useState<KnowledgeDomain | "new" | null>(null);
  const [deleting, setDeleting] = useState<KnowledgeDomain | null>(null);
  const [pendingDomain, setPendingDomain] = useState<KnowledgeDomain | null>(null);
  const [dialogPending, setDialogPending] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
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
    {error && <p className="error-notice" role="alert">{error}</p>}
    {domains.isError && <KnowledgeReadError error={domains.error} retry={() => void domains.refetch()} />}
    {domains.isPending && <p role="status">Loading domains…</p>}
    <div className="knowledge-domain-grid">{rows.map((domain) => <section key={domain.id} className="settings-card knowledge-domain-card" aria-label={`${domain.name} domain`}>
      <div className="settings-card-heading"><div><span className="section-label">{domain.prefix}</span><h2>{domain.name}</h2></div><span className="knowledge-domain-count">{domain.decision_count} document{domain.decision_count === 1 ? "" : "s"}</span></div>
      <p className="field-hint">Updated {formatDateTime(domain.updated_at)} · Prefix {domain.prefix} is permanent.</p>
      {domain.is_product_decision_domain && <p className="field-hint">Product Decision Records retain Needs Attention questions and answers. This built-in domain cannot be deleted.</p>}
      <div className="artifact-actions knowledge-domain-actions">
        <button className="button button-secondary" disabled={blocked} onClick={() => setEditing(domain)}>Rename</button>
        <button className="button button-secondary" onClick={() => { setError(""); void downloadKnowledgeFile(knowledgePath(projectId, `/domains/${domain.id}/export`), `${domain.prefix}-decisions.tar.gz`).catch((failure) => setError(errorMessage(failure))); }}>Export</button>
        <button className="button button-danger" disabled={blocked || domain.is_product_decision_domain} title={domain.is_product_decision_domain ? "Product Decision Records preserve human question and answer history." : undefined} onClick={() => setDeleting(domain)}>Delete</button>
      </div>
      <DomainDecisionReviewSettings domain={domain} unavailable={domains.isError} refreshing={domains.isFetching} blocked={dialogPending || pendingDomain !== null && pendingDomain.id !== domain.id}
        onPendingChange={(pending) => { setPendingDomain(pending ? domain : null); onPendingChange(pending); }}
        onReload={() => void domains.refetch()} onSaved={(saved) => { setNotice(`Automatic decision impact review settings for “${saved.name}” saved.`); refresh(); }} />
    </section>)}</div>
    {domains.hasNextPage && <button className="button button-secondary" disabled={domains.isFetchingNextPage} onClick={() => void domains.fetchNextPage()}>More domains</button>}
    {editing && <DomainDialog projectId={projectId} domain={editing === "new" ? undefined : editing} onPendingChange={changeDialogPending} onClose={() => setEditing(null)} onSaved={(saved) => { setEditing(null); setNotice(`Domain “${saved.name}” saved.`); refresh(); }} />}
    {deleting && <KnowledgeDeleteDialog projectId={projectId} target={{ kind: "domain", id: deleting.id, name: deleting.name }} onPendingChange={changeDialogPending} onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); setNotice("Domain deleted. Its deletion record remains available to agents."); refresh(); }} />}
  </section>;
}
