"use client";

import { useEffect, useId, useRef, useState } from "react";
import { artifactPath, decodeArtifact, decodeArtifactPage, type Artifact } from "@/lib/artifacts";
import { artifactUpdateIntent, dispatchArtifactMutation, type ArtifactMutation } from "@/lib/artifact-mutations";
import { readBoundedJson } from "@/lib/bounded-json";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { errorMessage } from "@/lib/api";
import { useCanonicalWorkSearch } from "@/components/use-canonical-work-search";

export function ArtifactPicker({ projectId, excludedIds, disabled, onSelect, workItemId }: {
  projectId: string; workItemId?: string; excludedIds: string[]; disabled: boolean; onSelect: (artifact: Artifact) => void;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [state, setState] = useState<{ scope: string; items: Artifact[]; total: number; error: string } | null>(null);
  const scope = `${projectId}:${query.trim()}`;
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ ...(query.trim() ? { q: query.trim() } : {}), limit: "10", sort: "filename", order: "asc" });
        const response = await fetch(`${artifactPath(projectId)}?${params}`, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("Unable to find artifacts. Try another search.");
        const page = decodeArtifactPage(await readBoundedJson(response, 1024 * 1024), projectId);
        if (!controller.signal.aborted) setState({ scope, items: page.items, total: page.total, error: "" });
      } catch (cause) { if (!controller.signal.aborted) setState({ scope, items: [], total: 0, error: errorMessage(cause) }); }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [projectId, query, scope]);
  const current = state?.scope === scope ? state : null;
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
  const [workQuery, setWorkQuery] = useState("");
  const [artifactLinkOpen, setArtifactLinkOpen] = useState(false);
  const [related, setRelated] = useState<Artifact[]>([]);
  const [relatedError, setRelatedError] = useState("");
  const workSearch = useCanonicalWorkSearch({ projectId: artifact.project_id, excludedWorkId: "", query: workQuery });
  const relatedKey = artifact.related_artifact_ids.join(",");
  useEffect(() => {
    const controller = new AbortController();
    setRelated([]); setRelatedError("");
    Promise.all(relatedKey ? relatedKey.split(",").map(async (id) => {
      const response = await fetch(artifactPath(artifact.project_id, id), { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error("Unable to load related artifact names.");
      return decodeArtifact(await readBoundedJson(response, 1024 * 1024), artifact.project_id, id);
    }) : []).then((items) => { if (!controller.signal.aborted) setRelated(items); }).catch((cause) => { if (!controller.signal.aborted) setRelatedError(errorMessage(cause)); });
    return () => controller.abort();
  }, [artifact.project_id, relatedKey]);
  const update = (changes: Parameters<typeof artifactUpdateIntent>[1]) => onUpdate(artifactUpdateIntent(artifact, changes, dashboardSessionId()));
  const workItems = workSearch.page?.items.filter((hit) => ![...artifact.related_work_item_ids, artifact.originating_work_item_id].includes(hit.summary.work_item.id));
  return <div className="artifact-metadata-editor">
    <div className="artifact-sensitivity"><div><strong>{artifact.sensitive ? "Sensitive artifact" : "Standard artifact"}</strong><p>Agents must obtain explicit human approval before each read or content search of a sensitive artifact. Approval is temporary and single use.</p></div><button type="button" className="button button-secondary" disabled={disabled || Boolean(artifact.deleted_at)} onClick={() => void update({ sensitive: !artifact.sensitive })}>{artifact.sensitive ? "Remove sensitive flag" : "Mark as sensitive"}</button></div>
    <div className="artifact-related-list"><h3>Related artifacts · {artifact.related_artifact_ids.length}</h3>{related.map((item) => <button type="button" className="text-button artifact-work-link" key={item.id} disabled={disabled} onClick={() => onOpenArtifact(item)}>{item.filename}{item.sensitive ? " · Sensitive" : ""}{item.deleted_at ? " · Deleted" : ""}</button>)}{!artifact.related_artifact_ids.length && <p>No related artifacts.</p>}{relatedError && <p role="alert">{relatedError}</p>}</div>
    {!artifact.deleted_at && <div className="artifact-link-columns">
      <details onToggle={(event) => setArtifactLinkOpen(event.currentTarget.open)}><summary>Link another artifact</summary>{artifactLinkOpen && <ArtifactPicker projectId={artifact.project_id} excludedIds={[artifact.id, ...artifact.related_artifact_ids]} disabled={disabled} onSelect={(item) => void update({ related_artifact_ids: [item.id] })} />}</details>
      <details><summary>Link a work item</summary><div className="artifact-link-picker"><label>Find a work item<input value={workQuery} maxLength={200} disabled={disabled} placeholder="Search work titles and summaries…" onChange={(event) => setWorkQuery(event.target.value)} /></label>{workSearch.searching && <p role="status">Finding work items…</p>}{workSearch.error && <p role="alert">{workSearch.error}</p>}<ul>{workItems?.map(({ summary: { work_item: item } }) => <li key={item.id}><span><bdi>{item.title}</bdi><small>{item.status} · {item.id}</small></span><button type="button" className="button button-secondary" disabled={disabled} onClick={() => void update({ related_work_item_ids: [item.id] })} aria-label={`Link work item ${item.title}`}>Link</button></li>)}</ul>{workSearch.searchedQuery && workSearch.page && !workItems?.length && <p>No unlinked work items found.</p>}</div></details>
    </div>}
    <p className="artifact-history-note">Links are retained in both directions. Linking and sensitivity changes are recorded in the audit log.</p>
  </div>;
}

export function WorkArtifactLinkDialog({ projectId, workItemId, linkedIds, onClose, onLinked }: {
  projectId: string; workItemId: string; linkedIds: string[]; onClose: () => void; onLinked: () => void;
}) {
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
    if (outcome.type === "success") { pendingRef.current = null; setPending(null); onLinked(); return; }
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
