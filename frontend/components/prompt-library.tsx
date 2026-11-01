"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { invalidateCoreReads } from "@/lib/core-queries";
import { invalidateWorkSettingsReads } from "@/lib/work-detail-queries";
import { promptDetailOptions, promptLibraryOptions, secondaryKeys } from "@/lib/secondary-queries";
import { formatDateTime } from "@/components/work-item-card";
import { api, ApiError, errorMessage } from "@/lib/api";
import { formatArtifactSize } from "@/lib/artifacts";
import {
  decodePromptDetail, promptContentLimit, promptPath, validPromptContent,
  type PromptDetail, type PromptMetadata
} from "@/lib/prompts";
import type { Project } from "@/lib/types";
import { useSearchShortcut } from "@/components/use-search-shortcut";

export default function PromptLibrary({ project, onNotice, onPendingChange }: {
  project: Project;
  onNotice: (message: string, error?: boolean) => void;
  onPendingChange: (pending: boolean) => void;
}) {
  const client = useQueryClient();
  const searchInput = useSearchShortcut();
  const library = useQuery(promptLibraryOptions(project.id));
  const page = library.data;
  const loading = library.isFetching;
  const error = library.error ? errorMessage(library.error) : "";
  const [selected, setSelected] = useState<PromptMetadata | null>(null);
  const [query, setQuery] = useState("");
  const refresh = () => void library.refetch({ cancelRefetch: false });

  const items = page?.items.filter((item) => `${item.name} ${item.description}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <section className="artifact-library prompt-library" aria-label="Prompt library">
    <div className="artifact-search-panel">
      <label className="section-label" htmlFor="prompt-search">Find a prompt</label>
      <div className="artifact-search">
        <div className="search-field artifact-search-field">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" aria-hidden="true"><circle cx="10.5" cy="10.5" r="8.5" /><path d="m17 17 4 4" /></svg>
          <input ref={searchInput} id="prompt-search" type="search" aria-keyshortcuts="/" placeholder="Search prompts by name or purpose…" value={query} onChange={(event) => setQuery(event.target.value)} />
          {!query && <kbd aria-hidden="true">/</kbd>}
        </div>
        {query && <button type="button" className="button button-secondary" onClick={() => setQuery("")}>Clear</button>}
      </div>
    </div>
    <p className="artifact-filter-note">Customize the instructions Mnemonic sends to agents in {project.name}. Select a prompt to edit its Markdown.</p>
    <div className="artifact-directory-heading"><h2>Project prompts{page && <span className="artifact-count">{page.items.length}</span>}</h2><button type="button" className="button button-secondary" disabled={loading} onClick={refresh}>Refresh</button></div>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button type="button" className="button button-secondary" onClick={refresh}>Retry loading prompts</button></div>}
    {(!error || page) && <>
      <div className="artifact-table-scroll" aria-busy={loading} tabIndex={0} role="region" aria-label="Prompt directory">
        <table className="artifact-table prompt-table"><thead><tr>{["Name / purpose", "Size", "Created", "Updated"].map((label) => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{items?.map((prompt) => <tr key={prompt.id}>
          <td><button type="button" className="artifact-name" aria-haspopup="dialog" onClick={() => setSelected(prompt)}><svg width="19" height="22" viewBox="0 0 20 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="M3 1h8l6 6v16H3zM11 1v7h6M6 13h8M6 17h6" /></svg><span>{prompt.name}</span></button><span className="artifact-type prompt-purpose">{prompt.description}</span></td>
          <td>{formatArtifactSize(prompt.size_bytes)}</td><td><time dateTime={prompt.created_at}>{formatDateTime(prompt.created_at)}</time></td><td><time dateTime={prompt.updated_at}>{formatDateTime(prompt.updated_at)}</time></td>
        </tr>)}</tbody></table>
      </div>
      {loading && !page && <p className="loading-state" role="status">Loading prompts…</p>}
      {items?.length === 0 && <p className="artifact-empty">No prompts match this name or purpose.</p>}
    </>}
    {page && <section className="macro-legend prompt-glossary" aria-labelledby="prompt-macros-title">
      <span className="section-label">AVAILABLE MACROS</span><h3 id="prompt-macros-title">Macro glossary</h3>
      <p>Use these values in any prompt. Mnemonic expands them when sending the prompt to an agent. Unknown macros and values unavailable in the current context remain unchanged.</p>
      <dl>{page.macros.map(({ macro, description }) => <div key={macro}><dt><code>{macro}</code></dt><dd>{description}</dd></div>)}</dl>
    </section>}
    {selected && <PromptEditor key={`${project.id}:${selected.id}`} project={project} prompt={selected} onClose={() => setSelected(null)} onPendingChange={onPendingChange} onSaved={(saved) => {
      void invalidateCoreReads(client, project.id, ["settings"]);
      void invalidateWorkSettingsReads(client, project.id);
      onNotice(`${saved.name} saved for “${project.name}”.`);
    }} />}
  </section>;
}

function PromptEditor({ project, prompt, onClose, onSaved, onPendingChange }: {
  project: Project;
  prompt: PromptMetadata;
  onClose: () => void;
  onSaved: (prompt: PromptDetail) => void;
  onPendingChange: (pending: boolean) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const contentId = useId();
  const [saved, setSaved] = useState<PromptDetail | null>(null);
  const [draft, setDraft] = useState("");
  const client = useQueryClient();
  const detail = useQuery(promptDetailOptions(project.id, prompt.id));
  const [reviewing, setReviewing] = useState(false);
  const loading = detail.isFetching || reviewing;
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const refresh = () => void detail.refetch({ cancelRefetch: false });
  const [needsReview, setNeedsReview] = useState(false);
  const [latest, setLatest] = useState<PromptDetail | null>(null);
  const mounted = useRef(true);
  const dirty = saved !== null && draft !== saved.content;

  useEffect(() => { onPendingChange(dirty || saving || needsReview); }, [dirty, saving, needsReview, onPendingChange]);
  useEffect(() => () => onPendingChange(false), [onPendingChange]);

  useEffect(() => {
    mounted.current = true;
    const dialog = dialogRef.current!;
    const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      mounted.current = false;
      dialog.close(); document.body.style.overflow = previousOverflow;
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => {
    if (!dirty && !saving) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty, saving]);
  // Reopening may initially show cached text. Adopt a fresh read while the
  // editor is still clean, but preserve any draft and its conflict baseline.
  useEffect(() => {
    if (detail.data && (!saved || !saving && !needsReview && draft === saved.content)) {
      setSaved(detail.data); setDraft(detail.data.content);
    }
  }, [detail.data, saved, draft, saving, needsReview]);

  function close() {
    if (saving) return;
    if (dirty && !window.confirm("Discard unsaved changes to this prompt?")) return;
    onClose();
  }
  async function reviewLatest() {
    setReviewing(true); setLatest(null);
    try {
      const result = await detail.refetch({ cancelRefetch: true, throwOnError: true });
      const current = result.data!;
      if (mounted.current) setLatest(current);
    } catch (failure) { if (mounted.current) setError(errorMessage(failure)); }
    finally { if (mounted.current) setReviewing(false); }
  }
  async function save() {
    if (!saved || needsReview || !validPromptContent(prompt.id, draft)) return;
    setSaving(true); setError(""); setCopyStatus("");
    try {
      const value = await api<unknown>(promptPath(project.id, prompt.id), {
        method: "PUT", body: JSON.stringify({ content: draft, expected_revision: saved.revision })
      });
      const result = decodePromptDetail(value, prompt.id);
      if (result.content !== draft) throw new Error("The saved content differs from your draft. Review the latest saved prompt before retrying.");
      if (!mounted.current) return;
      setSaved(result); setDraft(result.content);
      void client.cancelQueries({ queryKey: secondaryKeys.prompt(project.id, prompt.id), exact: true });
      client.setQueryData(secondaryKeys.prompt(project.id, prompt.id), result);
      onSaved(result);
    } catch (failure) {
      if (!mounted.current) return;
      if (!(failure instanceof ApiError) || failure.status === 0 || failure.status >= 500 || failure.status === 409) {
        setNeedsReview(true);
        setError(failure instanceof ApiError && failure.status === 409
          ? "This prompt changed since you opened it. Your edits have been kept. Review the current saved prompt before applying your draft."
          : "The save outcome is uncertain. Your edits have been kept. Review the current saved prompt before saving again.");
        await reviewLatest();
      } else setError(errorMessage(failure));
    } finally { if (mounted.current) setSaving(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(draft); setCopyStatus("Contents copied."); }
    catch { setCopyStatus("Unable to copy. Select the contents and copy them manually."); }
  }

  return <dialog ref={dialogRef} className="artifact-preview-drawer prompt-editor-drawer" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); close(); }}
    onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
    }}>
    <header className="artifact-preview-header"><div><span className="section-label">Project prompt</span><h2 id={titleId}>{prompt.name}</h2></div><div className="artifact-preview-actions">
      <button type="button" className="icon-button" aria-label="Copy contents" title="Copy contents" disabled={!saved} onClick={() => void copy()}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3" /></svg></button>
      <button type="button" className="icon-button" aria-label="Close prompt" title="Close prompt (Esc)" autoFocus disabled={saving} onClick={close}><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" /></svg></button>
    </div></header>
    <div className="artifact-preview-body prompt-editor-body">
      <p className="settings-intro">{prompt.description}</p>
      {copyStatus && <p role="status">{copyStatus}</p>}
      {(error || detail.error) && <div className="error-notice" role="alert"><p>{error || errorMessage(detail.error)}</p>{!saved && <button type="button" className="button button-secondary" disabled={loading} onClick={refresh}>Retry loading prompt</button>}</div>}
      {loading && <p role="status">Loading prompt…</p>}
      {needsReview && <section className="prompt-conflict" aria-label="Review saved prompt">
        {latest ? <><h3>Current saved prompt</h3><pre tabIndex={0}>{latest.content}</pre><button type="button" className="button button-secondary" disabled={saving} onClick={() => { setSaved(latest); setNeedsReview(false); setLatest(null); setError(""); }}>I reviewed the saved prompt</button></>
          : <button type="button" className="button button-secondary" disabled={loading} onClick={() => void reviewLatest()}>Load current saved prompt</button>}
      </section>}
      {saved && <label className="field prompt-editor-field" htmlFor={contentId}>Prompt content
        <textarea id={contentId} className="artifact-preview-text prompt-editor-text" spellCheck={false} value={draft} disabled={saving} onChange={(event) => { setDraft(event.target.value); setCopyStatus(""); }} />
        <span className="field-hint">Markdown · All available macros are supported. {promptContentLimit(prompt.id)}</span>
      </label>}
    </div>
    <footer className="prompt-editor-footer"><span className="field-hint">{saved ? `${formatArtifactSize(new TextEncoder().encode(draft).length)} · ${dirty ? "Unsaved changes" : "Saved"}` : "Prompt content unavailable"}</span><button type="button" className="button button-primary" disabled={!saved || loading || saving || needsReview || !dirty || !validPromptContent(prompt.id, draft)} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button></footer>
  </dialog>;
}
