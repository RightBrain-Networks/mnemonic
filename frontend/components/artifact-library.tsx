"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { artifactLibraryPath, artifactLocation, artifactPath, decodeArtifactPage, formatArtifactSize, type Artifact, type ArtifactPage, type ArtifactSort } from "@/lib/artifacts";
import { artifactMetadataHeader, dispatchArtifactMutation, type ArtifactMutation } from "@/lib/artifact-mutations";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { detailMessage, errorMessage } from "@/lib/api";
import { readBoundedJson } from "@/lib/bounded-json";
import { sameUuid, validUuid } from "@/lib/wire-guards";
import { formatDateTime } from "@/components/work-item-card";

const PAGE_SIZE = 50;
const columns: { key: ArtifactSort; label: string }[] = [
  { key: "filename", label: "Name" }, { key: "size_bytes", label: "Size" },
  { key: "revision", label: "Revision" }, { key: "created_at", label: "Created" },
  { key: "modified_at", label: "Modified" }
];

export default function ArtifactLibrary({ projectId, maximumBytes, refreshSignal, onPendingChange }: {
  projectId: string;
  maximumBytes: number;
  refreshSignal: number;
  onPendingChange: (pending: boolean) => void;
}) {
  const [page, setPage] = useState<ArtifactPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [workFilter, setWorkFilter] = useState("");
  const [sort, setSort] = useState<ArtifactSort>("filename");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [offset, setOffset] = useState(0);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [description, setDescription] = useState("");
  const [originWork, setOriginWork] = useState("");
  const [relatedWork, setRelatedWork] = useState("");
  const [pending, setPending] = useState<ArtifactMutation | null>(null);
  const [busy, setBusy] = useState(false);
  const [safetyConflict, setSafetyConflict] = useState(false);
  const [selected, setSelected] = useState<Artifact | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<Artifact | null>(null);
  const pendingRef = useRef<ArtifactMutation | null>(null);
  const queue = useRef<File[]>([]);
  const dragDepth = useRef(0);

  useEffect(() => {
    const location = artifactLocation(window.location.search);
    const work = sameUuid(location.projectId, projectId) ? location.workItemId : null;
    setWorkFilter(work ?? ""); setOriginWork(work ?? "");
  }, [projectId]);

  useEffect(() => {
    const refreshVisible = () => { if (document.visibilityState === "visible") setRefresh((value) => value + 1); };
    const timer = window.setInterval(refreshVisible, 30000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setLoadError("");
    const query = new URLSearchParams({ sort, order, limit: String(PAGE_SIZE), offset: String(offset), include_deleted: String(includeDeleted) });
    if (search) query.set("q", search);
    if (workFilter) query.set("work_item_id", workFilter);
    void fetch(`${artifactPath(projectId)}?${query}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const result = await readBoundedJson(response, 4 * 1024 * 1024);
        if (!response.ok) throw new Error(detailMessage((result as { detail?: unknown }).detail).message || "Unable to load artifacts.");
        return decodeArtifactPage(result, projectId);
      })
      .then((result) => { if (!controller.signal.aborted) setPage(result); })
      .catch((error) => { if (!controller.signal.aborted) { setPage(null); setLoadError(errorMessage(error)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, search, sort, order, offset, includeDeleted, refresh, refreshSignal, workFilter]);

  useEffect(() => {
    if (!pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);

  function markPending(value: ArtifactMutation | null) {
    pendingRef.current = value; setPending(value); onPendingChange(value !== null);
  }

  async function execute(intent: ArtifactMutation): Promise<boolean> {
    markPending(intent); setBusy(true); setActionError(""); setSafetyConflict(false);
    const outcome = await dispatchArtifactMutation(intent);
    setBusy(false);
    if (outcome.type === "success") {
      markPending(null);
      setSelected((current) => current?.id === outcome.artifact.id ? outcome.artifact : current);
      setNotice(`${outcome.artifact.filename} ${intent.method === "DELETE" ? "deleted. Its metadata and audit history remain available." : intent.method === "PUT" ? `replaced. Revision ${outcome.artifact.revision} is ready.` : "uploaded."}`);
      setRefresh((value) => value + 1);
      return true;
    }
    if (outcome.type === "rejected") { markPending(null); queue.current = []; setRefresh((value) => value + 1); }
    setSafetyConflict(outcome.type === "safety_conflict");
    setActionError(outcome.message);
    return false;
  }

  function uploadIntent(file: File, replacement?: Artifact): ArtifactMutation {
    const linked = relatedWork.split(/[\s,]+/).filter(Boolean);
    if (originWork && !validUuid(originWork) || linked.some((id) => !validUuid(id)) || linked.length > 50) throw new Error("Use valid work item IDs, with up to 50 related work items.");
    return Object.freeze({
      method: replacement ? "PUT" : "POST",
      path: replacement ? `${artifactPath(projectId, replacement.id)}/content` : artifactPath(projectId),
      projectId, operationId: crypto.randomUUID(), file,
      ...(replacement ? { artifactId: replacement.id, expectedRevision: replacement.revision } : {}),
      metadata: artifactMetadataHeader({ filename: replacement?.filename ?? file.name, agent_session_id: dashboardSessionId(), actor_client: "dashboard", ...(description ? { description } : {}), ...(!replacement && originWork ? { work_item_id: originWork } : {}), ...(linked.length ? { related_work_item_ids: linked } : {}) })
    });
  }

  async function drainQueue() {
    while (queue.current.length && !pendingRef.current) {
      const file = queue.current.shift()!;
      try { if (!await execute(uploadIntent(file))) return; }
      catch (error) { queue.current = []; setActionError(errorMessage(error)); return; }
    }
  }

  async function upload(files: File[], replacement?: Artifact) {
    if (pendingRef.current) return;
    setActionError(""); setNotice("");
    if (!files.length) return;
    if (files.length > 20) { setActionError("Upload up to 20 files at a time."); return; }
    const oversized = files.find((file) => file.size > maximumBytes);
    if (oversized) { setActionError(`${oversized.name} exceeds the ${formatArtifactSize(maximumBytes)} per-file limit.`); return; }
    try {
      if (replacement) { await execute(uploadIntent(files[0], replacement)); return; }
      queue.current = files; await drainQueue();
    } catch (error) { setActionError(errorMessage(error)); }
  }

  const pasteFiles = useRef(upload);
  pasteFiles.current = upload;
  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (!files.length) return;
      event.preventDefault(); void pasteFiles.current(files);
    };
    document.addEventListener("paste", paste);
    return () => document.removeEventListener("paste", paste);
  }, []);

  async function remove(artifact: Artifact) {
    if (pendingRef.current || !window.confirm(`Delete “${artifact.filename}”? Its content will be permanently removed. Metadata and audit history are retained.`)) return;
    await execute(Object.freeze({
      method: "DELETE", path: artifactPath(projectId, artifact.id), projectId,
      artifactId: artifact.id, expectedRevision: artifact.revision, operationId: crypto.randomUUID(),
      metadata: artifactMetadataHeader({ agent_session_id: dashboardSessionId(), actor_client: "dashboard" })
    }));
  }

  function searchArtifacts(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setSearch(query.trim()); setOffset(0); }

  return <section className={`artifact-library ${dragging ? "is-dragging" : ""}`} aria-label="Artifact library"
    onDragEnter={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); dragDepth.current += 1; setDragging(true); } }}
    onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = pending ? "none" : "copy"; } }}
    onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
    onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); void upload(Array.from(event.dataTransfer.files)); }}>
    <div className="artifact-toolbar">
      <form className="artifact-search" onSubmit={searchArtifacts}><label className="sr-only" htmlFor="artifact-search">Search artifact metadata and audit history</label><input id="artifact-search" placeholder="Search names, metadata and audit history…" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} /><button className="button button-secondary" type="submit">Search</button></form>
      <button type="button" className="button button-primary" disabled={Boolean(pending)} onClick={() => input.current?.click()}>Upload files</button>
      <input ref={input} className="sr-only" aria-label="Upload artifact files" type="file" multiple tabIndex={-1} disabled={Boolean(pending)} onChange={(event) => { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      <input ref={replaceInput} className="sr-only" aria-label="Replace artifact content" type="file" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; if (file && replaceTarget.current) void upload([file], replaceTarget.current); event.target.value = ""; }} />
    </div>
    <div className="artifact-upload-hint"><span>Drop files here or paste a file from your clipboard. Up to {formatArtifactSize(maximumBytes)} per file.</span><label><input type="checkbox" checked={includeDeleted} onChange={(event) => { setIncludeDeleted(event.target.checked); setOffset(0); }} /> Show deleted</label></div>
    <details className="artifact-upload-options"><summary>Upload description and work links</summary><div className="artifact-upload-fields"><label>Description<textarea value={description} maxLength={4000} disabled={Boolean(pending)} onChange={(event) => setDescription(event.target.value)} rows={2} /></label><label>Originating work item ID<input value={originWork} disabled={Boolean(pending)} onChange={(event) => setOriginWork(event.target.value.trim())} placeholder="Optional work item UUID" /></label><label>Related work item IDs<input value={relatedWork} disabled={Boolean(pending)} onChange={(event) => setRelatedWork(event.target.value)} placeholder="Optional comma-separated UUIDs" /></label></div></details>
    {workFilter && <p className="artifact-filter-note">Showing files linked to work item <code>{workFilter}</code>. <button className="text-button" onClick={() => { setWorkFilter(""); setOffset(0); }}>Show all project artifacts</button></p>}
    {actionError && <div className="error-notice" role="alert"><p>{actionError}</p>{pending && <><p>Keep this page open to preserve the exact retry request. Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled={busy || safetyConflict} onClick={() => { void execute(pending).then((success) => { if (success) void drainQueue(); }); }}>{busy ? "Working…" : "Retry pending action"}</button></>}</div>}
    {notice && <p role="status" className="artifact-notice">{notice}</p>}
    {busy && <p role="status" className="artifact-notice"><span className="spinner" /> {pending?.method === "DELETE" ? "Deleting artifact…" : "Uploading artifact…"}</p>}
    {loadError ? <div className="error-notice" role="alert"><p>{loadError}</p><button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Retry loading artifacts</button></div> : <>
      <div className="artifact-table-scroll" aria-busy={loading} tabIndex={0} role="region" aria-label="Sortable artifact directory">
        <table className="artifact-table"><thead><tr>{columns.map((column) => <th key={column.key} scope="col" aria-sort={sort === column.key ? order === "asc" ? "ascending" : "descending" : "none"}><button onClick={() => { setSort(column.key); setOrder(sort === column.key && order === "asc" ? "desc" : "asc"); setOffset(0); }}>{column.label}<span aria-hidden="true">{sort === column.key ? order === "asc" ? " ↑" : " ↓" : " ↕"}</span></button></th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{page?.items.map((artifact) => <tr key={artifact.id} className={artifact.deleted_at ? "artifact-deleted" : ""}>
            <td><button className="artifact-name" title={artifact.filename} onClick={() => setSelected(selected?.id === artifact.id ? null : artifact)}><svg width="19" height="22" viewBox="0 0 20 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M3 1h8l6 6v16H3zM11 1v7h6M6 13h8M6 17h6" /></svg><span>{artifact.filename}</span></button><span className="artifact-type">{artifact.deleted_at ? "Deleted · " : ""}{artifact.mime_type || "Unknown file type"}</span></td>
            <td>{formatArtifactSize(artifact.size_bytes)}</td><td><span className="artifact-revision">r{artifact.revision}</span></td><td><time dateTime={artifact.created_at}>{formatDateTime(artifact.created_at)}</time></td><td><time dateTime={artifact.modified_at}>{formatDateTime(artifact.modified_at)}</time></td>
            <td><div className="artifact-actions">{artifact.content_available && <><a className="button button-secondary" href={`${artifactPath(projectId, artifact.id)}/content`} download={artifact.filename} aria-label={`Download ${artifact.filename}`}>Download</a><button className="button button-secondary" disabled={Boolean(pending)} aria-label={`Replace ${artifact.filename}`} onClick={() => { if (window.confirm(`Replace “${artifact.filename}”? The previous content will be permanently removed.`)) { replaceTarget.current = artifact; replaceInput.current?.click(); } }}>Replace</button><button className="button button-danger" disabled={Boolean(pending)} aria-label={`Delete ${artifact.filename}`} onClick={() => void remove(artifact)}>Delete</button></>}</div></td>
          </tr>)}</tbody></table>
      </div>
      {loading && !page && <div className="loading-state" role="status">Loading artifacts…</div>}
      {!loading && page && !page.items.length && <div className="artifact-empty"><h2>{search || workFilter ? "No matching artifacts." : "Your project files belong here."}</h2><p>{search || workFilter ? "Try another metadata search or clear the work filter." : "Keep documents, binaries and working files together with the work that created them."}</p></div>}
      {page && <div className="artifact-pagination"><span>{page.total ? `${offset + 1}–${Math.min(offset + page.items.length, page.total)} of ${page.total} artifacts` : "0 artifacts"}</span><div><button className="button button-secondary" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><button className="button button-secondary" disabled={loading || offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div></div>}
    </>}
    {selected && <section className="artifact-details" aria-label={`Metadata for ${selected.filename}`}><div className="artifact-detail-heading"><h2>{selected.filename}</h2><button className="button button-secondary" onClick={() => setSelected(null)}>Close details</button></div>{selected.description && <p>{selected.description}</p>}<dl className="metadata-grid"><div><dt>Artifact ID</dt><dd className="mono break-all">{selected.id}</dd></div><div><dt>Created by session</dt><dd className="mono break-all">{selected.created_by_agent_session_id || "Not recorded"}</dd></div><div className="span-two"><dt>SHA-256</dt><dd className="mono break-all">{selected.sha256}</dd></div><div><dt>Originating work item</dt><dd>{selected.originating_work_item_id ? <a href={`/?work=${selected.originating_work_item_id}`} onClick={(event) => { if (pending) event.preventDefault(); }}>{selected.originating_work_item_id}</a> : "Not linked"}</dd></div><div><dt>Related work items</dt><dd>{selected.related_work_item_ids.length ? selected.related_work_item_ids.map((id) => <a className="artifact-work-link" key={id} href={`/?work=${id}`} onClick={(event) => { if (pending) event.preventDefault(); }}>{id}</a>) : "None"}</dd></div></dl><p className="artifact-history-note">Revision metadata and the append-only audit log are available through the artifact history tools. Content search is unimplemented.</p></section>}
    {dragging && <div className="artifact-drop-overlay" aria-hidden="true">{pending ? "Resolve the pending action first" : "Drop files to upload"}</div>}
  </section>;
}

export function WorkArtifactLinks({ projectId, workItemId }: { projectId: string; workItemId: string }) {
  const [page, setPage] = useState<ArtifactPage | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(async (signal: AbortSignal) => {
    setError(false); setPage(null);
    try {
      const response = await fetch(`${artifactPath(projectId)}?work_item_id=${workItemId}&limit=5`, { cache: "no-store", signal });
      if (!response.ok) throw new Error();
      const page = decodeArtifactPage(await readBoundedJson(response, 1024 * 1024), projectId);
      if (!signal.aborted) setPage(page);
    } catch { if (!signal.aborted) setError(true); }
  }, [projectId, workItemId]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);
  return <section className="work-artifact-links"><span className="section-label">ARTIFACTS{page ? ` · ${page.total}` : ""}</span>{page?.items.map((artifact) => <a key={artifact.id} href={`${artifactPath(projectId, artifact.id)}/content`} download={artifact.filename}>{artifact.filename} <span>r{artifact.revision}</span></a>)}<a href={artifactLibraryPath(projectId, workItemId)}>{error ? "Open artifacts to retry loading linked files" : "View or upload linked files"}</a></section>;
}
