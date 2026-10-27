"use client";

import { TagList, TagSelector } from "@/components/work-tags";
import { useEffect, useId, useRef, useState } from "react";
import type { Artifact } from "@/lib/artifacts";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { artifactDetailOptions, artifactPageOptions, artifactStatusOptions, reconcileArtifactMutation } from "@/lib/artifact-queries";
import { artifactUpdateIntent, dispatchArtifactMutation, type ArtifactMutation } from "@/lib/artifact-mutations";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { errorMessage } from "@/lib/api";
import { useCanonicalWorkSearch } from "@/components/use-canonical-work-search";

export function ArtifactPicker({ projectId, excludedIds, disabled, onSelect, workItemId }: {
  projectId: string; workItemId?: string; excludedIds: string[]; disabled: boolean; onSelect: (artifact: Artifact) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 250);
    return () => clearTimeout(timer);
  }, [query]);
  const statusRead = useQuery(artifactStatusOptions());
  const ready = search === query.trim() && statusRead.data?.enabled === true && !statusRead.isError;
  const filters = { projectId, search, fulltext: false, semantic: false, includeDeleted: false, workItemId: "",
    sort: "filename" as const, order: "asc" as const, offset: 0, limit: 10 };
  const read = useQuery(artifactPageOptions(client, filters, ready));
  const failure = statusRead.error ?? read.error;
  const current = failure ? { items: [], total: 0, error: errorMessage(failure) }
    : statusRead.data?.enabled === false ? { items: [], total: 0, error: statusRead.data.message }
      : ready && read.data?.page ? { ...read.data.page, error: "" } : null;
  const items = current?.items.filter((item) => !item.deleted_at && (!workItemId || item.originating_work_item_id !== workItemId && !item.related_work_item_ids.includes(workItemId)) && !excludedIds.some((id) => id.toLowerCase() === item.id.toLowerCase()));
  return <div className="artifact-link-picker">
    <label htmlFor={id}>Find an artifact<input id={id} value={query} maxLength={200} disabled={disabled} placeholder="Search file names and metadata…" onChange={(event) => setQuery(event.target.value)} /></label>
    {!current ? <p role="status">Finding artifacts…</p> : current.error ? <p role="alert">{current.error}</p> : <>
      <ul>{items?.map((item) => <li key={item.id}><span><bdi>{item.filename}</bdi>{item.sensitive && <span className="artifact-sensitive-badge">Sensitive</span>}<small>r{item.revision} · {item.id}</small></span><button type="button" className="button button-secondary" disabled={disabled} onClick={() => onSelect(item)} aria-label={`Link artifact ${item.filename}`}>Link</button></li>)}</ul>
      {!items?.length && <p>No unlinked artifacts found. Try another search.</p>}
      {current.total > 10 && <p>Showing the first 10 matches. Refine your search to find more.</p>}
    </>}
  </div>;
}

export function ArtifactMetadataEditor({ artifact, disabled, onUpdate, onOpenArtifact }: {
  artifact: Artifact; disabled: boolean; onUpdate: (intent: ArtifactMutation) => Promise<boolean>; onOpenArtifact: (artifact: Artifact) => void;
}) {
  const [editingTags, setEditingTags] = useState(false);
  const [tagDraft, setTagDraft] = useState<string[]>([]);
  const [workQuery, setWorkQuery] = useState("");
  const [artifactLinkOpen, setArtifactLinkOpen] = useState(false);
  const client = useQueryClient();
  const workSearch = useCanonicalWorkSearch({ projectId: artifact.project_id, excludedWorkId: "", query: workQuery });
  const relatedReads = useQueries({ queries: artifact.related_artifact_ids.map((id) => artifactDetailOptions(client, artifact.project_id, id)) });
  const related = relatedReads.flatMap((read) => read.data && !read.isError ? [read.data] : []);
  const relatedError = relatedReads.find((read) => read.error)?.error;
  const update = (changes: Parameters<typeof artifactUpdateIntent>[1]) => onUpdate(artifactUpdateIntent(artifact, changes, dashboardSessionId()));
  const workItems = workSearch.page?.items.filter((hit) => ![...artifact.related_work_item_ids, artifact.originating_work_item_id].includes(hit.summary.work_item.id));
  return <div className="artifact-metadata-editor">
    <section className="artifact-tag-editor" aria-label="Artifact tags"><div className="artifact-tag-heading"><h3>Tags</h3>{!editingTags && !artifact.deleted_at && <button type="button" className="button button-secondary" disabled={disabled} onClick={() => { setTagDraft(artifact.tags ?? []); setEditingTags(true); }}>Edit tags</button>}</div>
      {editingTags ? <form onSubmit={(event) => { event.preventDefault(); void update({ tags: tagDraft.length ? tagDraft : null }).then((saved) => { if (saved) setEditingTags(false); }); }}>
        <TagSelector projectId={artifact.project_id} value={tagDraft} onChange={setTagDraft} disabled={disabled} />
        <div className="settings-actions"><button type="submit" className="button button-primary" disabled={disabled}>Save tags</button><button type="button" className="button button-secondary" disabled={disabled} onClick={() => setEditingTags(false)}>Cancel</button></div>
      </form> : artifact.tags?.length ? <TagList projectId={artifact.project_id} names={artifact.tags} /> : <p className="field-hint">No tags selected.</p>}
    </section>
    <div className="artifact-sensitivity"><div><strong>{artifact.sensitive ? "Sensitive artifact" : "Standard artifact"}</strong><p>Agents must obtain explicit human approval before each read or content search of a sensitive artifact. Approval is temporary and single use.</p></div><button type="button" className="button button-secondary" disabled={disabled || Boolean(artifact.deleted_at)} onClick={() => void update({ sensitive: !artifact.sensitive })}>{artifact.sensitive ? "Remove sensitive flag" : "Mark as sensitive"}</button></div>
    <div className="artifact-related-list"><h3>Related artifacts · {artifact.related_artifact_ids.length}</h3>{related.map((item) => <button type="button" className="text-button artifact-work-link" key={item.id} disabled={disabled} onClick={() => onOpenArtifact(item)}>{item.filename}{item.sensitive ? " · Sensitive" : ""}{item.deleted_at ? " · Deleted" : ""}</button>)}{!artifact.related_artifact_ids.length && <p>No related artifacts.</p>}{relatedError && <p role="alert">{errorMessage(relatedError)}</p>}</div>
    {!artifact.deleted_at && <div className="artifact-link-columns">
      <details onToggle={(event) => setArtifactLinkOpen(event.currentTarget.open)}><summary>Link another artifact</summary>{artifactLinkOpen && <ArtifactPicker projectId={artifact.project_id} excludedIds={[artifact.id, ...artifact.related_artifact_ids]} disabled={disabled} onSelect={(item) => void update({ related_artifact_ids: [item.id] })} />}</details>
      <details><summary>Link a work item</summary><div className="artifact-link-picker"><label>Find a work item<input value={workQuery} maxLength={200} disabled={disabled} placeholder="Search work titles and summaries…" onChange={(event) => setWorkQuery(event.target.value)} /></label>{workSearch.searching && <p role="status">Finding work items…</p>}{workSearch.error && <p role="alert">{workSearch.error}</p>}<ul>{workItems?.map(({ summary: { work_item: item } }) => <li key={item.id}><span><bdi>{item.title}</bdi><small>{item.status} · {item.id}</small></span><button type="button" className="button button-secondary" disabled={disabled} onClick={() => void update({ related_work_item_ids: [item.id] })} aria-label={`Link work item ${item.title}`}>Link</button></li>)}</ul>{workSearch.searchedQuery && workSearch.page && !workItems?.length && <p>No unlinked work items found.</p>}</div></details>
    </div>}
    <p className="artifact-history-note">Links are retained in both directions. Tag, linking and sensitivity changes are recorded in the audit log.</p>
  </div>;
}

export function WorkArtifactLinkDialog({ projectId, workItemId, linkedIds, onClose, onLinked }: {
  projectId: string; workItemId: string; linkedIds: string[]; onClose: () => void; onLinked: () => void;
}) {
  const client = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingRef = useRef<ArtifactMutation | null>(null);
  const [pending, setPending] = useState<ArtifactMutation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => { element.close(); if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    if (!pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);
  async function execute(intent: ArtifactMutation) {
    pendingRef.current = intent; setPending(intent); setBusy(true); setError("");
    const outcome = await dispatchArtifactMutation(intent);
    setBusy(false);
    if (outcome.type === "success") { pendingRef.current = null; setPending(null); void reconcileArtifactMutation(client, outcome.artifact); onLinked(); return; }
    if (outcome.type === "rejected") { pendingRef.current = null; setPending(null); }
    setConflict(outcome.type === "safety_conflict"); setError(outcome.message);
  }
  return <dialog ref={dialog} className="artifact-link-dialog" aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!pendingRef.current) onClose(); }}>
    <div className="artifact-detail-heading"><h2 id={titleId}>Link an existing artifact</h2><button type="button" className="button button-secondary" disabled={Boolean(pending)} onClick={onClose}>Close</button></div>
    <p>Choose a project artifact to link to this work item.</p>
    {error && <div className="error-notice" role="alert"><p>{error}</p>{pending && <><p>Keep this page open. The exact request is preserved. Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled={busy || conflict} onClick={() => void execute(pending)}>Retry pending link</button></>}</div>}
    {busy && <p role="status">Linking artifact…</p>}
    <ArtifactPicker projectId={projectId} workItemId={workItemId} excludedIds={linkedIds} disabled={Boolean(pending)} onSelect={(artifact) => { if (!pendingRef.current) void execute(artifactUpdateIntent(artifact, { related_work_item_ids: [workItemId] }, dashboardSessionId())); }} />
  </dialog>;
}
