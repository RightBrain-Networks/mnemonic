"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ARTIFACT_DISABLED_MESSAGE, artifactLibraryPath, artifactLocation, artifactPath, decodeArtifactLimitError, decodeArtifactPage, decodeArtifactSearchPage, fetchArtifactStatus, formatArtifactSize, type Artifact, type ArtifactPage, type ArtifactSearchPage, type ArtifactSort, type ArtifactStatus } from "@/lib/artifacts";
import { artifactMetadataHeader, dispatchArtifactMutation, type ArtifactMutation } from "@/lib/artifact-mutations";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { detailMessage, errorMessage } from "@/lib/api";
import { readBoundedJson } from "@/lib/bounded-json";
import { sameUuid, validUuid } from "@/lib/wire-guards";
import { formatDateTime } from "@/components/work-item-card";
import ArtifactPreviewDrawer from "@/components/artifact-preview-drawer";
import { artifactPreviewKind, type ArtifactPreviewKind } from "@/lib/artifact-preview";

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
  const [status, setStatus] = useState<ArtifactStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [fulltext, setFulltext] = useState(false);
  const [searchPage, setSearchPage] = useState<ArtifactSearchPage | null>(null);
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
  const [preview, setPreview] = useState<{ artifact: Artifact; kind: ArtifactPreviewKind } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  const replaceTarget = useRef<Artifact | null>(null);
  const pendingRef = useRef<ArtifactMutation | null>(null);
  const queue = useRef<File[]>([]);
  const dragDepth = useRef(0);
  const statusRef = useRef<ArtifactStatus | null>(null);
  const requestScopeRef = useRef<string | null>(null);
  const enabled = status?.enabled === true;
  const currentMaximum = status?.max_bytes ?? maximumBytes;
  const recordStatus = useCallback((value: ArtifactStatus | null) => {
    if (value?.enabled && statusRef.current?.enabled === false && pendingRef.current) setActionError("The library is enabled again. Retry the preserved pending action with the same file and operation ID.");
    if (!value?.enabled || statusRef.current && statusRef.current.max_bytes !== value.max_bytes) {
      setPage(null); setSearchPage(null);
    }
    statusRef.current = value; setStatus(value);
    if (!value?.enabled) { setDragging(false); dragDepth.current = 0; setPreview(null); }
  }, []);

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
    const scope = JSON.stringify([projectId, search, fulltext, sort, order, offset, includeDeleted, workFilter, maximumBytes]);
    if (requestScopeRef.current !== scope) { setSearchPage(null); setPage(null); }
    requestScopeRef.current = scope;
    setLoading(true); setLoadError("");
    const query = new URLSearchParams({ sort, order, limit: String(PAGE_SIZE), offset: String(offset), include_deleted: String(includeDeleted) });
    if (search) query.set("q", search);
    if (workFilter) query.set("work_item_id", workFilter);
    let checkedStatus = false;
    async function load() {
      try {
        const status = await fetchArtifactStatus(controller.signal);
        if (controller.signal.aborted) return;
        checkedStatus = true; recordStatus(status);
        if (!status.enabled) { setPage(null); setSelected(null); return; }
        const response = search ? await fetch(`${artifactPath(projectId)}/search-content`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ q: search, fulltext, include_deleted: includeDeleted, limit: PAGE_SIZE, offset, ...(workFilter ? { work_item_id: workFilter } : {}) }),
          cache: "no-store", signal: controller.signal
        }) : await fetch(`${artifactPath(projectId)}?${query}`, { cache: "no-store", signal: controller.signal });
        const result = await readBoundedJson(response, 4 * 1024 * 1024);
        if (controller.signal.aborted) return;
        const limitError = decodeArtifactLimitError(result, response.status);
        if (limitError?.code === "artifact_library_disabled") {
          recordStatus({ enabled: false, max_bytes: 0, message: limitError.message }); setPage(null); setSelected(null); return;
        }
        if (!response.ok) throw new Error(detailMessage((result as { detail?: unknown }).detail).message || "Unable to load artifacts.");
        let freshPage: ArtifactPage;
        if (search) {
          const matches = decodeArtifactSearchPage(result, projectId, fulltext, PAGE_SIZE, offset);
          if (!includeDeleted && matches.items.some((item) => item.artifact.deleted_at !== null)) throw new Error("Mnemonic returned deleted artifacts outside the requested search scope.");
          setSearchPage(matches); freshPage = { ...matches, items: matches.items.map((item) => item.artifact) };
        } else { setSearchPage(null); freshPage = decodeArtifactPage(result, projectId); }
        setPage(freshPage);
        setSelected((current) => {
          if (!current) return null;
          const fresh = freshPage.items.find((item) => sameUuid(item.id, current.id));
          return fresh && fresh.revision >= current.revision ? fresh : current;
        });
      } catch (error) {
        if (!controller.signal.aborted) { if (!checkedStatus) recordStatus(null); setPage(null); setSearchPage(null); setLoadError(errorMessage(error)); }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }
    void load();
    return () => controller.abort();
  }, [projectId, search, fulltext, sort, order, offset, includeDeleted, refresh, refreshSignal, workFilter, maximumBytes, recordStatus]);

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
    if (!statusRef.current?.enabled) {
      setActionError(statusRef.current?.message || "Check artifact status before retrying the pending action."); return false;
    }
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
    if (outcome.type === "disabled") {
      recordStatus({ enabled: false, max_bytes: 0, message: outcome.message }); setPage(null); setSelected(null);
    }
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
    while (queue.current.length && !pendingRef.current && statusRef.current?.enabled) {
      const file = queue.current.shift()!;
      try { if (!await execute(uploadIntent(file))) return; }
      catch (error) { queue.current = []; setActionError(errorMessage(error)); return; }
    }
  }

  async function upload(files: File[], replacement?: Artifact) {
    if (pendingRef.current || !statusRef.current?.enabled) return;
    setActionError(""); setNotice("");
    if (!files.length) return;
    if (files.length > 20) { setActionError("Upload up to 20 files at a time."); return; }
    const oversized = files.find((file) => file.size > statusRef.current!.max_bytes);
    if (oversized) { setActionError(`${oversized.name} exceeds the ${formatArtifactSize(currentMaximum)} (${currentMaximum.toLocaleString("en-US")} bytes) per-file limit.`); return; }
    try {
      if (replacement) { await execute(uploadIntent(files[0], replacement)); return; }
      queue.current = files; await drainQueue();
    } catch (error) { setActionError(errorMessage(error)); }
  }

  const pasteFiles = useRef(upload);
  pasteFiles.current = upload;
  useEffect(() => {
    if (!enabled || preview) return;
    const paste = (event: ClipboardEvent) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (!files.length) return;
      event.preventDefault(); void pasteFiles.current(files);
    };
    document.addEventListener("paste", paste);
    return () => document.removeEventListener("paste", paste);
  }, [enabled, preview]);

  async function remove(artifact: Artifact) {
    if (!statusRef.current?.enabled || pendingRef.current || !window.confirm(`Delete “${artifact.filename}”? Its content will be permanently removed. Metadata and audit history are retained.`)) return;
    await execute(Object.freeze({
      method: "DELETE", path: artifactPath(projectId, artifact.id), projectId,
      artifactId: artifact.id, expectedRevision: artifact.revision, operationId: crypto.randomUUID(),
      metadata: artifactMetadataHeader({ agent_session_id: dashboardSessionId(), actor_client: "dashboard" })
    }));
  }

  function searchArtifacts(event: FormEvent<HTMLFormElement>) { event.preventDefault(); setSearch(query.trim()); setOffset(0); }

  if (!enabled) {
    const disabled = status?.enabled === false;
    return <section className="artifact-library" aria-label="Artifact library" onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "none"; }} onDrop={(event) => event.preventDefault()}>
      <div className="artifact-empty" role="status"><h2>{disabled ? "Artifact library disabled" : loading ? "Checking artifact status…" : "Artifact status unavailable"}</h2><p>{disabled ? status?.message || ARTIFACT_DISABLED_MESSAGE : loadError || "Checking the configured per-file limit before loading files."}</p>{disabled && <p>The configured per-file limit is 0 bytes. Existing files are preserved.</p>}<button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Check artifact status</button></div>
      {pending && <div className="error-notice" role="alert"><p>{disabled ? "The library is disabled, so this pending action cannot be retried yet." : "Artifact status must be available before this pending action can be retried."}</p><p>Keep this page open. Your exact pending request{pending.file ? ` and file “${pending.file.name}”` : ""} are preserved. Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled>Retry pending action</button></div>}
    </section>;
  }

  return <section className={`artifact-library ${dragging ? "is-dragging" : ""}`} aria-label="Artifact library"
    onDragEnter={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); dragDepth.current += 1; setDragging(true); } }}
    onDragOver={(event) => { if (event.dataTransfer.types.includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = pending ? "none" : "copy"; } }}
    onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
    onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); void upload(Array.from(event.dataTransfer.files)); }}>
    <div className="artifact-toolbar">
      <form className="artifact-search" onSubmit={searchArtifacts}><label className="sr-only" htmlFor="artifact-search">Search artifact metadata and content</label><input id="artifact-search" placeholder="Search artifact names and metadata…" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} /><button className="button button-secondary" type="submit">Search</button>{search && <button className="button button-secondary" type="button" onClick={() => { setQuery(""); setSearch(""); setOffset(0); }}>Clear search</button>}</form>
      <button type="button" className="button button-primary" disabled={Boolean(pending)} onClick={() => input.current?.click()}>Upload files</button>
      <input ref={input} className="sr-only" aria-label="Upload artifact files" type="file" multiple tabIndex={-1} disabled={Boolean(pending)} onChange={(event) => { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      <input ref={replaceInput} className="sr-only" aria-label="Replace artifact content" type="file" tabIndex={-1} onChange={(event) => { const file = event.target.files?.[0]; if (file && replaceTarget.current) void upload([file], replaceTarget.current); event.target.value = ""; }} />
    </div>
    <div className="artifact-search-options"><label><input type="checkbox" checked={fulltext} onChange={(event) => { setFulltext(event.target.checked); setOffset(0); }} /> Search file contents too</label><span>{fulltext ? "Includes extracted text from current files. All query terms must match." : "Metadata only, including extracted document properties. File contents are excluded."}</span></div>
    <div className="artifact-upload-hint"><span>Drop files here or paste a file from your clipboard. Up to {formatArtifactSize(currentMaximum)} ({currentMaximum.toLocaleString("en-US")} bytes) per file.</span><label><input type="checkbox" checked={includeDeleted} onChange={(event) => { setIncludeDeleted(event.target.checked); setOffset(0); }} /> Show deleted</label></div>
    <details className="artifact-upload-options"><summary>Upload description and work links</summary><div className="artifact-upload-fields"><label>Description<textarea value={description} maxLength={4000} disabled={Boolean(pending)} onChange={(event) => setDescription(event.target.value)} rows={2} /></label><label>Originating work item ID<input value={originWork} disabled={Boolean(pending)} onChange={(event) => setOriginWork(event.target.value.trim())} placeholder="Optional work item UUID" /></label><label>Related work item IDs<input value={relatedWork} disabled={Boolean(pending)} onChange={(event) => setRelatedWork(event.target.value)} placeholder="Optional comma-separated UUIDs" /></label></div></details>
    {workFilter && <p className="artifact-filter-note">Showing files linked to work item <code>{workFilter}</code>. <button className="text-button" onClick={() => { setWorkFilter(""); setOffset(0); }}>Show all project artifacts</button></p>}
    {actionError && <div className="error-notice" role="alert"><p>{actionError}</p>{pending && <><p>Keep this page open to preserve the exact retry request. Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled={busy || safetyConflict} onClick={() => { void execute(pending).then((success) => { if (success) void drainQueue(); }); }}>{busy ? "Working…" : "Retry pending action"}</button></>}</div>}
    {notice && <p role="status" className="artifact-notice">{notice}</p>}
    {busy && <p role="status" className="artifact-notice"><span className="spinner" /> {pending?.method === "DELETE" ? "Deleting artifact…" : "Uploading artifact…"}</p>}
    {searchPage && <div className="artifact-search-status" role="status"><p>Results ordered by relevance. Clear search to sort the directory.</p><p>{searchPage.indexing.ready} extracted · {searchPage.indexing.pending} pending · {searchPage.indexing.failed} failed{searchPage.indexing.truncated > 0 ? ` · ${searchPage.indexing.truncated} truncated` : ""}</p>{(searchPage.indexing.pending > 0 || searchPage.indexing.failed > 0 || searchPage.indexing.truncated > 0) && <p>Content results may be incomplete. Pending files refresh automatically; failed files still match their available metadata. Truncated files search only the extracted prefix.</p>}</div>}
    {loadError ? <div className="error-notice" role="alert"><p>{loadError}</p><button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Retry loading artifacts</button></div> : <>
      <div className="artifact-table-scroll" aria-busy={loading} tabIndex={0} role="region" aria-label="Sortable artifact directory">
        <table className="artifact-table"><thead><tr>{columns.map((column) => <th key={column.key} scope="col" aria-sort={!search && sort === column.key ? order === "asc" ? "ascending" : "descending" : "none"}><button disabled={Boolean(search)} title={search ? "Clear search to sort the directory" : undefined} onClick={() => { setSort(column.key); setOrder(sort === column.key && order === "asc" ? "desc" : "asc"); setOffset(0); }}>{column.label}<span aria-hidden="true">{search ? "" : sort === column.key ? order === "asc" ? " ↑" : " ↓" : " ↕"}</span></button></th>)}<th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{page?.items.map((artifact) => <tr key={artifact.id} className={artifact.deleted_at ? "artifact-deleted" : ""}>
            <td><button className="artifact-name" title={artifact.filename} onClick={() => setSelected(selected?.id === artifact.id ? null : artifact)}><svg width="19" height="22" viewBox="0 0 20 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M3 1h8l6 6v16H3zM11 1v7h6M6 13h8M6 17h6" /></svg><span>{artifact.filename}</span></button><span className="artifact-type">{artifact.deleted_at ? "Deleted · " : ""}{artifact.mime_type || "Unknown file type"}</span>{searchPage && <ArtifactSearchExcerpt page={searchPage} artifactId={artifact.id} />}</td>
            <td>{formatArtifactSize(artifact.size_bytes)}</td><td><span className="artifact-revision">r{artifact.revision}</span></td><td><time dateTime={artifact.created_at}>{formatDateTime(artifact.created_at)}</time></td><td><time dateTime={artifact.modified_at}>{formatDateTime(artifact.modified_at)}</time></td>
            <td><div className="artifact-actions">{artifact.content_available && <>{artifactPreviewKind(artifact) && <button type="button" className="button button-secondary" aria-label={`View ${artifact.filename}`} onClick={() => { const kind = artifactPreviewKind(artifact); if (kind) setPreview({ artifact, kind }); }}>View</button>}<a className="button button-secondary" href={`${artifactPath(projectId, artifact.id)}/content`} download={artifact.filename} aria-label={`Download ${artifact.filename}`}>Download</a><button className="button button-secondary" disabled={Boolean(pending)} aria-label={`Replace ${artifact.filename}`} onClick={() => { if (window.confirm(`Replace “${artifact.filename}”? The previous content will be permanently removed.`)) { replaceTarget.current = artifact; replaceInput.current?.click(); } }}>Replace</button><button className="button button-danger" disabled={Boolean(pending)} aria-label={`Delete ${artifact.filename}`} onClick={() => void remove(artifact)}>Delete</button></>}</div></td>
          </tr>)}</tbody></table>
      </div>
      {loading && !page && <div className="loading-state" role="status">Loading artifacts…</div>}
      {page && !page.items.length && <div className="artifact-empty"><h2>{search || workFilter ? "No matching artifacts." : "Your project files belong here."}</h2><p>{search || workFilter ? `Try another query${fulltext ? "" : " or opt in to file-content search"}, or clear the work filter.` : "Keep documents, binaries and working files together with the work that created them."}</p></div>}
      {page && <div className="artifact-pagination"><span>{page.total ? `${offset + 1}–${Math.min(offset + page.items.length, page.total)} of ${page.total} artifacts` : "0 artifacts"}</span><div><button className="button button-secondary" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button><button className="button button-secondary" disabled={loading || offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button></div></div>}
    </>}
    {selected && <section className="artifact-details" aria-label={`Metadata for ${selected.filename}`}><div className="artifact-detail-heading"><h2>{selected.filename}</h2><button className="button button-secondary" onClick={() => setSelected(null)}>Close details</button></div>{selected.description && <p>{selected.description}</p>}<dl className="metadata-grid"><div><dt>Artifact ID</dt><dd className="mono break-all">{selected.id}</dd></div><div><dt>Created by session</dt><dd className="mono break-all">{selected.created_by_agent_session_id || "Not recorded"}</dd></div><div className="span-two"><dt>SHA-256</dt><dd className="mono break-all">{selected.sha256}</dd></div><div><dt>Originating work item</dt><dd>{selected.originating_work_item_id ? <a href={`/?work=${selected.originating_work_item_id}`} onClick={(event) => { if (pending) event.preventDefault(); }}>{selected.originating_work_item_id}</a> : "Not linked"}</dd></div><div><dt>Related work items</dt><dd>{selected.related_work_item_ids.length ? selected.related_work_item_ids.map((id) => <a className="artifact-work-link" key={id} href={`/?work=${id}`} onClick={(event) => { if (pending) event.preventDefault(); }}>{id}</a>) : "None"}</dd></div></dl><ArtifactExtractionDetails artifact={selected} /><p className="artifact-history-note">Revision metadata and the append-only audit log are available through the artifact history tools. Extracted properties and search snippets are untrusted file data.</p></section>}
    {preview && <ArtifactPreviewDrawer key={`${preview.artifact.project_id}:${preview.artifact.id}`} artifact={preview.artifact} kind={preview.kind} onClose={() => setPreview(null)} />}
    {dragging && <div className="artifact-drop-overlay" aria-hidden="true">{pending ? "Resolve the pending action first" : "Drop files to upload"}</div>}
  </section>;
}

function ArtifactSearchExcerpt({ page, artifactId }: { page: ArtifactSearchPage; artifactId: string }) {
  const match = page.items.find((item) => item.artifact.id === artifactId);
  if (!match) return null;
  return <div className="artifact-search-excerpt"><span className="artifact-match-fields">Matched {match.matched_fields.join(" + ")}</span>{match.snippet && <p>{match.snippet}</p>}</div>;
}

function ArtifactExtractionDetails({ artifact }: { artifact: Artifact }) {
  const extraction = artifact.extraction;
  return <div className="artifact-extraction-metadata"><h3>Extracted document properties</h3><p>Text extraction: {extraction.status}{extraction.truncated ? " · truncated" : ""}{extraction.error_code ? ` · ${extraction.error_code}` : ""}{extraction.extracted_at ? ` · ${formatDateTime(extraction.extracted_at)}` : ""}</p>{Object.keys(extraction.metadata).length ? <dl className="metadata-grid">{Object.entries(extraction.metadata).map(([key, values]) => <div key={key}><dt>{key}</dt><dd>{values.join("\n")}</dd></div>)}</dl> : <p>No extracted document properties available.</p>}</div>;
}

export function WorkArtifactLinks({ projectId, workItemId }: { projectId: string; workItemId: string }) {
  const [page, setPage] = useState<ArtifactPage | null>(null);
  const [status, setStatus] = useState<ArtifactStatus | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(async (signal: AbortSignal) => {
    setError(false); setPage(null);
    try {
      const status = await fetchArtifactStatus(signal);
      if (signal.aborted) return;
      setStatus(status);
      if (!status.enabled) return;
      const response = await fetch(`${artifactPath(projectId)}?work_item_id=${workItemId}&limit=5`, { cache: "no-store", signal });
      const value = await readBoundedJson(response, 1024 * 1024);
      if (signal.aborted) return;
      const limitError = decodeArtifactLimitError(value, response.status);
      if (limitError?.code === "artifact_library_disabled") { setStatus({ enabled: false, max_bytes: 0, message: limitError.message }); return; }
      if (!response.ok) throw new Error();
      const page = decodeArtifactPage(value, projectId);
      if (!signal.aborted) setPage(page);
    } catch { if (!signal.aborted) { setStatus(null); setError(true); } }
  }, [projectId, workItemId]);
  useEffect(() => {
    let controller = new AbortController();
    const refresh = () => { if (document.visibilityState === "visible") { controller.abort(); controller = new AbortController(); void load(controller.signal); } };
    void load(controller.signal);
    const timer = window.setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", refresh);
    return () => { controller.abort(); window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [load]);
  if (status?.enabled === false) return <section className="work-artifact-links"><span className="section-label">ARTIFACTS</span><p role="status">Artifact library disabled. {status.message}</p><a href={artifactLibraryPath(projectId, workItemId)}>Open artifact library</a></section>;
  if (!status) return <section className="work-artifact-links"><span className="section-label">ARTIFACTS</span><p role="status">{error ? "Artifact status is unavailable." : "Checking artifact status…"}</p><a href={artifactLibraryPath(projectId, workItemId)}>Open artifact library</a></section>;
  return <section className="work-artifact-links"><span className="section-label">ARTIFACTS{page ? ` · ${page.total}` : ""}</span>{page?.items.map((artifact) => <a key={artifact.id} href={`${artifactPath(projectId, artifact.id)}/content`} download={artifact.filename}>{artifact.filename} <span>r{artifact.revision}</span></a>)}<a href={artifactLibraryPath(projectId, workItemId)}>{error ? "Open artifacts to retry loading linked files" : "View or upload linked files"}</a></section>;
}
