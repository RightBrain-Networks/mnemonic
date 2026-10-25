"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { DecisionImpactView, DecisionAssessments } from "@/components/knowledge-impact";
import CommitLookup, { ScopeEvidence } from "@/components/knowledge-scope";
import Link from "@/components/dashboard-link";
import DocumentPreviewDrawer, { DocumentPreviewText } from "@/components/document-preview-drawer";
import { DecisionStateBadge, KnowledgePagination, KnowledgeReadError as ReadError, stateLabel } from "@/components/knowledge-shared";
import DecisionEditor from "@/components/knowledge-decision-editor";
import KnowledgeDeleteDialog from "@/components/knowledge-delete-dialog";
import { downloadKnowledgeFile } from "@/lib/knowledge-management";
import { errorMessage } from "@/lib/api";
import { formatArtifactSize, artifactLibraryPath } from "@/lib/artifacts";
import { formatDateTime } from "@/lib/display-time";
import { decisionStates, knowledgeLibraryPath, knowledgeNextOffset, knowledgePath, selectedDecision, type Decision, type DecisionState } from "@/lib/knowledge";
import { decisionContentOptions, decisionHistoryOptions, decisionsOptions, decisionScopeOptions, knowledgeKeys, readDomainPage } from "@/lib/knowledge-queries";

type Selection = { id: string; revision?: number };

export default function KnowledgeLibrary({ projectId, repositoryUrl, query = "", onPendingChange }: {
  projectId: string; repositoryUrl: string | null; query?: string; onPendingChange: (pending: boolean) => void;
}) {
  const client = useQueryClient();
  const [domainId, setDomainId] = useState("");
  const [state, setState] = useState<DecisionState | "">("");
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Selection | null>(() => selectedDecision(query));
  const [editing, setEditing] = useState<{ decision?: Decision; replace?: boolean } | null>(null);
  const [deleting, setDeleting] = useState<Decision | null>(null);
  const [pending, setPending] = useState(false);
  const changePending = (value: boolean) => { setPending(value); onPendingChange(value); };
  const [notice, setNotice] = useState("");
  const domains = useInfiniteQuery({
    queryKey: knowledgeKeys.domains(projectId), initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => readDomainPage(projectId, pageParam, signal),
    getNextPageParam: (last) => knowledgeNextOffset(last), retry: false,
    refetchInterval: 30_000, refetchIntervalInBackground: false
  });
  const allDomains = domains.data?.pages.flatMap((page) => page.items) ?? [];
  const domain = allDomains.find((item) => item.id === domainId);
  const decisions = useQuery(decisionsOptions(projectId, { domainId, state, q: search, offset }));
  const page = decisions.isError ? undefined : decisions.data;

  useEffect(() => { setSelected(selectedDecision(query)); }, [query]);
  function chooseDomain(id: string) { setDomainId(id); setOffset(0); }
  function submitSearch(event: FormEvent) { event.preventDefault(); setSearch(draft.trim()); setOffset(0); }

  return <section className="artifact-library knowledge-library" aria-label="Decision documents">
    <div className="artifact-search-panel">
      <label className="section-label" htmlFor="decision-search">Find a decision</label>
      <form className="artifact-search" onSubmit={submitSearch}>
        <div className="search-field artifact-search-field">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" aria-hidden="true"><circle cx="10.5" cy="10.5" r="8.5" /><path d="m17 17 4 4" /></svg>
          <input id="decision-search" type="search" aria-label="Search decision titles and contents" placeholder="Search decisions…" value={draft} maxLength={1000} onChange={(event) => setDraft(event.target.value)} />
        </div>
        <div className="artifact-search-actions"><button className="button button-primary" type="submit">Search</button>
          {(draft || search) && <button className="button button-secondary" type="button" onClick={() => { setDraft(""); setSearch(""); setOffset(0); }}>Clear</button>}
        </div>
      </form>
    </div>
    <CommitLookup projectId={projectId} repositoryUrl={repositoryUrl} onDecision={(id, revision) => setSelected({ id, revision })} />
    <div className="filter-row knowledge-domain-bar">
      <div className="status-filters knowledge-domain-tabs" role="group" aria-label="Filter by domain">
        <button type="button" className={`filter-button ${!domainId ? "selected" : ""}`} aria-pressed={!domainId} onClick={() => chooseDomain("")}>All domains</button>
        {allDomains.map((item) => <button key={item.id} type="button" className={`filter-button ${domainId === item.id ? "selected" : ""}`} aria-pressed={domainId === item.id} onClick={() => chooseDomain(item.id)} title={`${item.prefix} · ${item.decision_count} decisions`}>{item.name}<span className="knowledge-prefix">{item.prefix}</span></button>)}
        {domains.hasNextPage && <button type="button" className="filter-button" disabled={domains.isFetchingNextPage} onClick={() => void domains.fetchNextPage()}>More domains</button>}
      </div>
      <button className="button button-primary" type="button" disabled={pending || !allDomains.length} onClick={() => { setNotice(""); setEditing({}); }}>Upload</button>
    </div>
    {domains.isError && <ReadError error={domains.error} retry={() => void domains.refetch()} />}
    {notice && <p className="artifact-notice" role="status">{notice}</p>}
    <div className="artifact-directory-heading">
      <h2>{domain?.name ?? "Project decisions"}{page && <span className="artifact-count">{page.total}</span>}
      </h2>

    </div>
    <div className="status-filters knowledge-state-filters" role="group" aria-label="Filter decisions by state">
      <button type="button" className={`filter-button ${!state ? "selected" : ""}`} aria-pressed={!state} onClick={() => { setState(""); setOffset(0); }}>All states</button>
      {decisionStates.map((value) => <button key={value} type="button" className={`filter-button ${state === value ? "selected" : ""}`} aria-pressed={state === value} onClick={() => { setState(value); setOffset(0); }}>{stateLabel(value)}</button>)}
    </div>
    <p className="artifact-history-note knowledge-current-note">Current revisions. Open a decision to view its retained history and the code that cites it.</p>
    {domain?.is_product_decision_domain && <p className="artifact-history-note">Needs Attention questions and recorded answers live in this domain. <Link href={`/attention?project=${projectId}`}>Review waiting product decisions</Link>.</p>}
    {decisions.isError ? <ReadError error={decisions.error} retry={() => void decisions.refetch()} /> : <>
      <div className="artifact-table-scroll" role="region" aria-label="Decision directory" tabIndex={0} aria-busy={decisions.isFetching}>
        <table className="artifact-table knowledge-table"><thead><tr><th scope="col">Decision</th><th scope="col">State</th><th scope="col">Revision commits</th><th scope="col">Modified</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{page?.items.map((decision) => <tr key={decision.id}>
            <td><button className="artifact-name" aria-haspopup="dialog" onClick={() => setSelected({ id: decision.decision_id, revision: decision.revision })}><svg width="19" height="22" viewBox="0 0 20 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M3 1h8l6 6v16H3zM11 1v7h6M6 13h8M6 17h6" /></svg><span>{decision.title}</span></button><span className="artifact-type"><code>{decision.reference}</code> · {decision.domain_name}</span></td>
            <td><DecisionStateBadge state={decision.state} /></td><td>{decision.scope_count}</td><td><time dateTime={decision.updated_at}>{formatDateTime(decision.updated_at)}</time></td>
            <td><div className="artifact-actions knowledge-row-actions"><button className="button button-secondary" aria-label={`View ${decision.reference}`} onClick={() => setSelected({ id: decision.decision_id, revision: decision.revision })}>View</button>
              <button className="button button-secondary" disabled={pending} aria-label={`Edit ${decision.reference}`} onClick={() => setEditing({ decision })}>Edit</button>
              <button className="button button-secondary" aria-label={`Download ${decision.reference}`} onClick={() => void downloadKnowledgeFile(knowledgePath(projectId, `/decisions/${decision.decision_id}/download?revision=${decision.revision}`), `${decision.reference}.${decision.media_type === "text/markdown" ? "md" : "txt"}`).catch((failure) => setNotice(errorMessage(failure)))}>Download</button>
              <button className="button button-secondary" disabled={pending} aria-label={`Replace ${decision.reference}`} onClick={() => setEditing({ decision, replace: true })}>Replace</button>
              <button className="button button-danger" disabled={pending} aria-label={`Delete ${decision.reference}`} onClick={() => setDeleting(decision)}>Delete</button>
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>
      {decisions.isPending && <div className="loading-state" role="status">Loading decisions…</div>}
      {page && !page.items.length && <div className="artifact-empty"><h2>{search || state ? "No matching decisions." : allDomains.length ? "Decisions belong here." : "Start with a decision domain."}</h2><p>{search || state ? "Try another search or state filter." : allDomains.length ? "Upload a Markdown or plain text file, or ask an agent to add a decision." : "Group architecture, product, or other decisions under a name and permanent prefix."}</p></div>}
      {page && <KnowledgePagination page={page} noun="decisions" busy={decisions.isFetching} onOffset={setOffset} />}
    </>}
    {editing && <DecisionEditor projectId={projectId} domains={allDomains} initialDomainId={domainId} decision={editing.decision} replace={editing.replace}
      onPendingChange={changePending} onClose={() => setEditing(null)} onSaved={(saved) => {
        setEditing(null); setNotice(`Decision “${saved.title}” saved as revision ${saved.revision}.`);
        void client.invalidateQueries({ queryKey: knowledgeKeys.root(projectId) });
      }} />}
    {deleting && <KnowledgeDeleteDialog projectId={projectId} target={{ kind: "decision", id: deleting.decision_id, name: deleting.title }} onPendingChange={changePending}
      onClose={() => setDeleting(null)} onDeleted={() => { setDeleting(null); setNotice("Decision deleted. Its deletion record remains available to agents."); void client.invalidateQueries({ queryKey: knowledgeKeys.root(projectId) }); }} />}
    {selected && <DecisionDrawer projectId={projectId} onPendingChange={onPendingChange} selected={selected} onSelect={setSelected} onClose={() => setSelected(null)} />}
  </section>;
}


function DecisionDrawer({ projectId, selected, onSelect, onClose, onPendingChange }: { projectId: string; selected: Selection; onSelect: (selection: Selection) => void; onClose: () => void; onPendingChange: (pending: boolean) => void }) {
  const [pending, setPending] = useState(false);
  const changePending = (value: boolean) => { setPending(value); onPendingChange(value); };
  const openDecision = (id: string) => { const target = selectedDecision(new URLSearchParams({ decision: id }).toString()); if (target && !pending) onSelect(target); };
  const [tab, setTab] = useState<"document" | "details" | "history" | "scope" | "impact" | "assessments">("document");
  const [historyOffset, setHistoryOffset] = useState(0);
  const [scopeOffset, setScopeOffset] = useState(0);
  const content = useQuery({ ...decisionContentOptions(projectId, selected.id, selected.revision), enabled: !pending });
  const decision = content.isError && !pending ? undefined : content.data;
  const history = useQuery(decisionHistoryOptions(projectId, selected.id, historyOffset, tab === "history"));
  const scope = useQuery(decisionScopeOptions(projectId, selected.id, scopeOffset, tab === "scope"));
  useEffect(() => { setTab("document"); setHistoryOffset(0); setScopeOffset(0); }, [selected.id]);
  function openRevision(revision?: number) { onSelect({ id: selected.id, revision }); setTab("document"); }

  return <DocumentPreviewDrawer title={decision?.title ?? selected.id} label="Decision document" text={decision?.content ?? null} className="decision-preview-drawer" closeDisabled={pending} onClose={onClose}>
    <div className="decision-preview-summary">
      <code>{decision?.reference ?? `${selected.id}${selected.revision === undefined ? "" : `-${selected.revision}`}`}</code>
      {decision && <DecisionStateBadge state={decision.state} />}
      {selected.revision !== undefined && <button className="text-button" disabled={pending} onClick={() => openRevision()}>View current revision</button>}
    </div>
    {decision?.human_gate && <div className="decision-attention-origin">
      <p>{decision.human_gate.status === "unresolved" ? "This Product Decision Record is waiting for a human answer." : "This Product Decision Record retains the human answer and its question history."}</p>
      {decision.human_gate.work_project_id !== projectId && <p>The originating work was moved to another project before Product Decision Records were introduced. This historical record remains in its original project.</p>}
      {decision.human_gate.status === "unresolved" && <Link className="text-link" href={`/attention?project=${decision.human_gate.work_project_id}&work_item_id=${decision.human_gate.work_item_id}`}>Answer in Needs Attention</Link>}
      <Link className="text-link" href={`/work-items?project=${decision.human_gate.work_project_id}&work=${decision.human_gate.work_item_id}`}>View originating work</Link>
    </div>}
    <div className="status-filters decision-preview-tabs" role="group" aria-label="Decision views">
      {(["document", "details", "history", "scope", "impact", "assessments"] as const).map((value) => <button type="button" key={value} disabled={pending} className={`filter-button ${tab === value ? "selected" : ""}`} aria-pressed={tab === value} onClick={() => setTab(value)}>{({ document: "Document", details: "Details & links", history: "Revisions", scope: "Code scope", impact: "Impact", assessments: "Assessments" })[value]}</button>)}
    </div>
    <div className={`artifact-preview-body ${tab === "document" ? "" : "decision-metadata-body"}`}>
      {content.isError && !pending ? <ReadError error={content.error} retry={() => void content.refetch()} /> : !decision ? <p role="status">Loading decision…</p>
        : tab === "document" ? <DocumentPreviewText text={decision.content} markdown={decision.media_type === "text/markdown"} />
          : tab === "details" ? <DecisionDetails decision={decision} onDecision={openDecision} />
            : tab === "impact" ? <DecisionImpactView key={decision.reference} projectId={projectId} decisionId={decision.decision_id} revision={decision.revision} onDecision={openDecision} />
            : tab === "assessments" ? <DecisionAssessments key={decision.decision_id} projectId={projectId} decisionId={decision.decision_id} onDecision={openDecision} onPendingChange={changePending} />
            : tab === "history" ? <>
              <p className="artifact-history-note">Every revision is retained. Select one to read the exact document that was cited.</p>
              {history.isError ? <ReadError error={history.error} retry={() => void history.refetch()} /> : history.isPending ? <p role="status">Loading revisions…</p> : <>
                <ol className="decision-history">{history.data.items.map((revision) => <li key={revision.reference}><button className="text-button" onClick={() => openRevision(revision.revision)}>{revision.reference}</button><DecisionStateBadge state={revision.state} /><strong>{revision.title}</strong><span>{formatDateTime(revision.updated_at)} · {authorLabel(revision)}</span></li>)}</ol>
                <KnowledgePagination page={history.data} noun="revisions" busy={history.isFetching} onOffset={setHistoryOffset} />
              </>}
            </> : <>
              <p className="artifact-history-note">Registered citations for every retained revision. Each reference pins the revision considered. Repository identities and merged commits are author assertions; Mnemonic does not verify Git history.</p>
              {scope.isError ? <ReadError error={scope.error} retry={() => void scope.refetch()} /> : scope.isPending ? <p role="status">Loading code scope…</p> : <>
                {!scope.data.items.length && <p>No code references recorded.</p>}
                <ol className="decision-history">{scope.data.items.map((entry) => <li key={entry.id}><ScopeEvidence projectId={projectId} entry={entry} /><button className="text-button" onClick={() => openRevision(entry.revision)}>{entry.decision_id}-{entry.revision}</button><span>{formatDateTime(entry.created_at)}</span></li>)}</ol>
                <KnowledgePagination page={scope.data} noun="code references" busy={scope.isFetching} onOffset={setScopeOffset} />
              </>}
            </>}
    </div>
  </DocumentPreviewDrawer>;
}

function authorLabel(decision: Decision) { return decision.author_kind === "human" ? "Human created" : `${decision.model_id ?? "Model not recorded"} · ${decision.agent_session_id ?? "Session not recorded"}`; }
function DecisionDetails({ decision, onDecision }: { decision: Decision; onDecision: (id: string) => void }) {
  const decisionLinks = (values: string[]): ReactNode => values.length ? values.map((id) => <button key={id} className="text-button artifact-work-link" onClick={() => onDecision(id)}>{id}</button>) : "None";
  return <>
    <dl className="metadata-grid">
      <div><dt>Domain</dt><dd>{decision.domain_name} ({decision.prefix})</dd></div><div><dt>Size</dt><dd>{formatArtifactSize(decision.size_bytes)} · UTF-8</dd></div>
      <div><dt>Created</dt><dd>{formatDateTime(decision.created_at)}</dd></div><div><dt>Modified</dt><dd>{formatDateTime(decision.updated_at)}</dd></div>
      <div className="span-two"><dt>Author</dt><dd className="break-all">{authorLabel(decision)}</dd></div><div className="span-two"><dt>SHA-256</dt><dd className="mono break-all">{decision.sha256}</dd></div>
      <div><dt>Supersedes</dt><dd>{decisionLinks(decision.supersedes)}</dd></div><div><dt>Superseded by</dt><dd>{decisionLinks(decision.superseded_by)}</dd></div>
      <div className="span-two"><dt>Related decisions</dt><dd>{decisionLinks(decision.related_decision_ids)}</dd></div>
      <div className="span-two"><dt>Referenced by</dt><dd>{decisionLinks(decision.referenced_by)}</dd></div>
      <div className="span-two"><dt>Related work items</dt><dd>{decision.related_work_item_ids.length ? decision.related_work_item_ids.map((id) => <Link key={id} className="artifact-work-link" href={`/work-items?project=${decision.project_id}&work=${id}`}>{id}</Link>) : "None"}</dd></div>
      <div className="span-two"><dt>Related artifacts</dt><dd>{decision.related_artifact_ids.length ? decision.related_artifact_ids.map((id) => <Link key={id} className="artifact-work-link" href={`${artifactLibraryPath(decision.project_id)}&artifact=${id}`}>{id}</Link>) : "None"}</dd></div>
    </dl>
    <section className="decision-dependencies"><h3>Depends on</h3>
      {decision.depends_on?.length ? <ul className="decision-history">{decision.depends_on.map((edge) => <li key={`${edge.decision_id}-${edge.revision}`}><button className="text-button" onClick={() => onDecision(`${edge.decision_id}-${edge.revision}`)}>{edge.decision_id}-{edge.revision}</button><p>{edge.rationale}</p></li>)}</ul> : <p>No dependencies recorded. Related links do not establish dependencies.</p>}
      <h3>Supersession details</h3>
      {decision.supersession_details?.length ? <ul className="decision-history">{decision.supersession_details.map((edge) => <li key={`${edge.decision_id}-${edge.revision}`}><button className="text-button" onClick={() => onDecision(`${edge.decision_id}-${edge.revision}`)}>{edge.decision_id}-{edge.revision}</button><strong>{edge.extent === "full" ? "Full supersession" : "Partial supersession"}</strong><p>{edge.reason}</p></li>)}</ul> : <p>No extent or reason recorded.</p>}
      {decision.supersedes.filter((id) => !decision.supersession_details?.some((edge) => edge.decision_id === id)).map((id) => <p key={id}>{id}: supersession extent and rationale unknown.</p>)}
    </section>
    <Link href={knowledgeLibraryPath(decision.project_id, decision.decision_id, decision.revision)}>Link to this revision</Link>
  </>;
}
