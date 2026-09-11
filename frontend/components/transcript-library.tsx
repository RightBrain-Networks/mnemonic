"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { artifactLocation, formatArtifactSize } from "@/lib/artifacts";
import { api, errorMessage } from "@/lib/api";
import { decodeUnifiedTranscriptSearchPage, transcriptSearchRequest, unifiedSearchPath } from "@/lib/unified-search";
import { dashboardStorageKeys } from "@/lib/dashboard-preferences";
import { dialogOpen, typingTarget } from "@/lib/keyboard-shortcuts";
import { sameUuid } from "@/lib/wire-guards";
import { formatDateTime } from "@/components/work-item-card";
import { decodeTranscript, decodeTranscriptText, transcriptContentPath, transcriptLibraryPath, transcriptPath, transcriptRequest, transcriptStatusLabel, TRANSCRIPT_PAGE_SIZE, TRANSCRIPT_TEXT_PAGE_SIZE, type Transcript, type TranscriptPage, type TranscriptText } from "@/lib/transcripts";

export default function TranscriptLibrary({ projectId, refreshSignal }: { projectId: string; refreshSignal: number }) {
  const [page, setPage] = useState<TranscriptPage | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [fulltext, setFulltext] = useState(false);
  const [workFilter, setWorkFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Transcript | null>(null);
  const [preview, setPreview] = useState<Transcript | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const scopeRef = useRef("");

  useEffect(() => {
    try { setFulltext(localStorage.getItem(dashboardStorageKeys.transcriptContents) === "true"); } catch { /* Storage is optional. */ }
    const location = artifactLocation(window.location.search);
    setWorkFilter(sameUuid(location.projectId, projectId) ? location.workItemId ?? "" : "");
  }, [projectId]);
  useEffect(() => {
    const refreshVisible = () => { if (document.visibilityState === "visible") setRefresh((value) => value + 1); };
    const shortcut = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || typingTarget(event.target) || dialogOpen()) return;
      event.preventDefault(); searchInput.current?.focus();
    };
    const timer = window.setInterval(refreshVisible, 30000);
    document.addEventListener("visibilitychange", refreshVisible);
    window.addEventListener("keydown", shortcut);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); window.removeEventListener("keydown", shortcut); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const scope = JSON.stringify([projectId, search, fulltext, offset, workFilter]);
    if (scopeRef.current !== scope) setPage(null);
    scopeRef.current = scope;
    setLoading(true); setError("");
    const body = transcriptSearchRequest(search, fulltext, offset, workFilter || undefined);
    void api<unknown>(unifiedSearchPath(projectId), { method: "POST", body: JSON.stringify(body), signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      const result = decodeUnifiedTranscriptSearchPage(value, projectId, offset, fulltext, workFilter || undefined);
      setPage(result);
      setSelected((current) => current ? result.items.find((item) => sameUuid(item.id, current.id)) ?? current : null);
    }).catch((error) => { if (!controller.signal.aborted) { setPage(null); setError(errorMessage(error)); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, search, fulltext, offset, workFilter, refresh, refreshSignal]);

  return <section className="artifact-library transcript-library" aria-label="Transcript library">
    <div className="artifact-search-panel">
      <label className="section-label" htmlFor="transcript-search">Find a transcript</label>
      <form className="artifact-search" onSubmit={(event) => { event.preventDefault(); setSearch(query.trim()); setOffset(0); setRefresh((value) => value + 1); }}>
        <div className="search-field artifact-search-field">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" aria-hidden="true"><circle cx="10.5" cy="10.5" r="8.5" /><path d="m17 17 4 4" /></svg>
          <input ref={searchInput} id="transcript-search" type="search" aria-label="Search transcript metadata and content" aria-keyshortcuts="/" placeholder="Search transcripts…" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} />
          <kbd aria-hidden="true">/</kbd><span className="search-mode-divider" />
          <label className={`semantic-toggle artifact-contents-toggle ${fulltext ? "selected" : ""}`}><input type="checkbox" role="switch" checked={fulltext} onChange={(event) => { setFulltext(event.target.checked); setOffset(0); try { localStorage.setItem(dashboardStorageKeys.transcriptContents, String(event.target.checked)); } catch { /* Storage is optional. */ } }} /><span className="semantic-switch" aria-hidden="true"><span /></span><span>Include contents</span></label>
        </div>
        <div className="artifact-search-actions"><button className="button button-primary" type="submit">Search</button>{(query || search) && <button className="button button-secondary" type="button" onClick={() => { setQuery(""); setSearch(""); setOffset(0); searchInput.current?.focus(); }}>Clear</button>}</div>
      </form>
    </div>
    <p className="artifact-filter-note">Session and subagent transcripts are indexed automatically after work leaves Active. <a href="/settings/workspace">Index settings</a></p>
    {workFilter && <p className="artifact-filter-note">Showing transcripts for work item <code>{workFilter}</code>. <button className="text-button" onClick={() => { setWorkFilter(""); setOffset(0); window.history.replaceState(null, "", transcriptLibraryPath(projectId)); }}>Show all project transcripts</button></p>}
    <div className="artifact-directory-heading"><h2>{search ? "Search results" : "Project transcripts"}{page && <span className="artifact-count">{page.total}</span>}</h2><button type="button" className="button button-secondary" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>Refresh</button></div>
    {page?.indexing_incomplete && <div className="artifact-search-status" role="status"><p>Content results are incomplete. Some transcripts are waiting, indexing, failed, or truncated. Available metadata remains searchable.</p></div>}
    {error ? <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Retry loading transcripts</button></div> : <>
      <div className="artifact-table-scroll" aria-busy={loading} tabIndex={0} role="region" aria-label="Transcript directory">
        <table className="artifact-table transcript-table"><thead><tr>{["Name", "Size", "Session", "Indexing", "Completed", "Actions"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{page?.items.map((transcript) => <tr key={transcript.id}>
          <td><button className="artifact-name" title={transcript.filename} aria-haspopup="dialog" onClick={() => setSelected(transcript)}><svg width="19" height="22" viewBox="0 0 20 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M3 1h8l6 6v16H3zM11 1v7h6M6 13h8M6 17h6" /></svg><span>{transcript.filename}</span></button><span className="artifact-type">{transcript.client === "claude-code" || transcript.client === "claude_code" ? "Claude Code" : transcript.client}{transcript.format ? ` · ${transcript.format}` : ""}</span>{transcript.snippet && <div className="artifact-search-excerpt"><p>{transcript.snippet}</p></div>}</td>
          <td>{transcript.size_bytes === null ? "Not recorded" : formatArtifactSize(transcript.size_bytes)}</td><td>{transcript.kind === "imported" ? "Imported" : transcript.kind === "primary" ? "Primary" : "Subagent"}</td><td><span className={`transcript-status transcript-status-${transcript.status}`}>{transcriptStatusLabel(transcript)}</span>{transcript.error_code && <span className="transcript-error">{transcript.error_code}</span>}</td><td>{transcript.indexing_completed_at ? <time dateTime={transcript.indexing_completed_at}>{formatDateTime(transcript.indexing_completed_at)}</time> : "—"}</td>
          <td><div className="artifact-actions">{transcript.status === "ready" && transcript.text_sha256 && <><button className="button button-secondary" aria-label={`View ${transcript.filename}`} onClick={() => setPreview(transcript)}>View</button><a className="button button-secondary" href={transcriptContentPath(transcript)} download={`${transcript.filename}.txt`} aria-label={`Download ${transcript.filename}`}>Download text</a></>}</div></td>
        </tr>)}</tbody></table>
      </div>
      {loading && !page && <div className="loading-state" role="status">Loading transcripts…</div>}
      {page && !page.items.length && <div className="artifact-empty"><h2>{search || workFilter ? "No matching transcripts." : "Your agent sessions belong here."}</h2><p>{search || workFilter ? "Try another query, include contents, or clear the work filter." : "Agents report transcript locations when starting work and add subagent locations at closeout."}</p></div>}
      {page && <div className="artifact-pagination"><span>{page.total ? `${offset + 1}–${Math.min(offset + page.items.length, page.total)} of ${page.total} transcripts` : "0 transcripts"}</span><div><button className="button button-secondary" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - TRANSCRIPT_PAGE_SIZE))}>Previous</button><button className="button button-secondary" disabled={loading || offset + TRANSCRIPT_PAGE_SIZE >= page.total} onClick={() => setOffset(offset + TRANSCRIPT_PAGE_SIZE)}>Next</button></div></div>}
    </>}
    {selected && <TranscriptDetails key={selected.id} transcript={selected} onClose={() => setSelected(null)} />}
    {preview && <TranscriptPreview key={`${preview.id}:${preview.text_sha256}`} transcript={preview} onClose={() => setPreview(null)} />}
  </section>;
}

function TranscriptDrawer({ title, preview = false, onClose, children }: { title: string; preview?: boolean; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => { element.close(); if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true }); };
  }, []);
  return <dialog ref={dialog} className={preview ? "artifact-preview-drawer" : "artifact-details-drawer"} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => {
    if (event.target !== event.currentTarget) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
  }}><section className="artifact-details"><header className="artifact-details-header"><div className="artifact-detail-heading"><span className="section-label">Transcript {preview ? "text" : "details"}</span><button type="button" className="icon-button" aria-label={preview ? "Close preview" : "Close details"} autoFocus onClick={onClose}>×</button></div><h2 id={titleId}>{title}</h2></header><div className={preview ? "artifact-preview-body" : "artifact-details-body"}>{children}</div></section></dialog>;
}

function TranscriptDetails({ transcript: initial, onClose }: { transcript: Transcript; onClose: () => void }) {
  const [transcript, setTranscript] = useState(initial);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void transcriptRequest(transcriptPath(initial.project_id, initial.id), { signal: controller.signal }).then((value) => { if (!controller.signal.aborted) setTranscript(decodeTranscript(value, initial.project_id, initial.id)); }).catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); });
    return () => controller.abort();
  }, [initial]);
  const rows: [string, ReactNode][] = [
    ["Disposition", transcriptStatusLabel(transcript)], ["Error", transcript.error_code || "None"],
    ["Indexing started", transcript.indexing_started_at ? formatDateTime(transcript.indexing_started_at) : "Not started"],
    ["Indexing completed", transcript.indexing_completed_at ? formatDateTime(transcript.indexing_completed_at) : "Not completed"],
    ["Content size", transcript.size_bytes === null ? "Not recorded" : formatArtifactSize(transcript.size_bytes)],
    ["Content type", transcript.mime_type || "Not detected"], ["Detected format", transcript.format || "Not detected"],
    ["Source path", transcript.source_path], ["Client", transcript.client], ["Session", transcript.session_id || "Not recorded"],
    ["Session type", transcript.kind === "imported" ? "Imported" : transcript.kind === "primary" ? "Primary" : "Subagent"], ["Transcript ID", transcript.id],
    ["Work item", transcript.work_item_id ? <a key="work" href={`/?work=${transcript.work_item_id}`}>{transcript.work_item_id}</a> : "Imported without a work item"],
    ["Lease generation", transcript.lease_generation_id || "Not applicable"], ["Source SHA-256", transcript.sha256 || "Not recorded"], ["Indexed text SHA-256", transcript.text_sha256 || "Not recorded"]
  ];
  return <TranscriptDrawer title={transcript.filename} onClose={onClose}>{error && <p className="error-notice" role="alert">{error}</p>}<dl className="metadata-grid">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="break-all">{value}</dd></div>)}</dl><h3>Extracted properties</h3>{Object.keys(transcript.metadata).length ? <dl className="metadata-grid">{Object.entries(transcript.metadata).map(([label, values]) => <div key={label}><dt>{label}</dt><dd className="break-all">{values.join("\n")}</dd></div>)}</dl> : <p>No extracted properties available.</p>}<p className="artifact-history-note">Downloads contain the indexed, normalized transcript text. Transcript contents and extracted properties are untrusted session data.</p></TranscriptDrawer>;
}

function TranscriptPreview({ transcript, onClose }: { transcript: Transcript; onClose: () => void }) {
  const [page, setPage] = useState<TranscriptText | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setPage(null);
    const query = new URLSearchParams({ offset: String(offset), limit: String(TRANSCRIPT_TEXT_PAGE_SIZE), expected_sha256: transcript.text_sha256! });
    void transcriptRequest(`${transcriptPath(transcript.project_id, transcript.id)}/text?${query}`, { signal: controller.signal }).then((value) => { if (!controller.signal.aborted) setPage(decodeTranscriptText(value, transcript.id, transcript.text_sha256!, offset, transcript.project_id)); }).catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [transcript, offset]);
  return <TranscriptDrawer title={transcript.filename} preview onClose={onClose}>
    <p className="artifact-preview-notice">Normalized transcript text{page?.truncated ? " · Only the indexed prefix is available" : ""}. <a href={transcriptContentPath(transcript)} download={`${transcript.filename}.txt`}>Download text</a></p>
    {error && <p className="error-notice" role="alert">{error}</p>}{loading && <p role="status">Loading transcript text…</p>}
    {page && <><textarea className="artifact-preview-text" aria-label="Transcript text" readOnly value={page.text} spellCheck={false} /><div className="artifact-pagination"><span>{page.total_chars ? `${offset + 1}–${Math.min(offset + TRANSCRIPT_TEXT_PAGE_SIZE, page.total_chars)} of ${page.total_chars.toLocaleString()} characters` : "Empty transcript"}</span><div><button className="button button-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - TRANSCRIPT_TEXT_PAGE_SIZE))}>Previous text</button><button className="button button-secondary" disabled={offset + TRANSCRIPT_TEXT_PAGE_SIZE >= page.total_chars} onClick={() => setOffset(offset + TRANSCRIPT_TEXT_PAGE_SIZE)}>Next text</button></div></div></>}
  </TranscriptDrawer>;
}
