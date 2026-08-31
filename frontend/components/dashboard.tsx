"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, errorMessage, handoffPath } from "@/lib/api";
import type { Handoff, HandoffPatch, HandoffStatus, HandoffSummary, Page, Project, StatusFilter } from "@/lib/types";

const PAGE_SIZE = 20;
const statusLabels: Record<StatusFilter, string> = { open: "Open", done: "Done", "wont-do": "Won’t do", promoted: "Promoted", all: "All" };
const filters: StatusFilter[] = ["open", "done", "wont-do", "promoted", "all"];
const icons = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  plus: "M12 5v14M5 12h14",
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  edit: "m16 3 5 5M3 21l5-1L21 7l-5-5L3 15v6Z",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7",
  refresh: "M20 7V2m0 5h-5M4 17v5m0-5h5M4 8a8 8 0 0 1 13-4l3 3M4 17l3 3a8 8 0 0 0 13-4",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6",
  external: "M14 3h7v7m0-7L10 14M10 3H3v18h18v-7",
  branch: "M6 3v12m0 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0-5h7a5 5 0 0 0 5-5m0-3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"
};

function Icon({ name, size = 18 }: { name: keyof typeof icons; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={icons[name]} /></svg>;
}

function Logo() {
  return <svg className="logo-mark" width="34" height="34" viewBox="0 0 34 34" fill="none" aria-hidden="true"><rect width="34" height="34" rx="10" fill="currentColor" /><path d="M9 24V10h4l4 7 4-7h4v14h-4v-8l-4 6-4-6v8H9Z" fill="#f9f8f3" /></svg>;
}

function clientLabel(client: string) {
  return ({ "claude-code": "Claude Code", chatgpt: "ChatGPT", opencode: "OpenCode", manual: "Manual capture" } as Record<string, string>)[client] ?? client;
}

function formatDate(value: string, includeTime = false) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", ...(includeTime ? { hour: "numeric", minute: "2-digit" } as const : {}) }).format(date);
}

function StatusBadge({ status }: { status: HandoffStatus }) {
  return <span className={`status-badge status-${status}`}><span />{statusLabels[status]}</span>;
}

function Dialog({ title, children, onClose, wide = false, busy = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; busy?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);
  return <dialog ref={ref} className={`dialog ${wide ? "dialog-wide" : ""}`} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose} disabled={busy}><Icon name="close" /></button></div>
    <div className="dialog-content">{children}</div>
  </dialog>;
}

function Provenance({ record }: { record: HandoffSummary }) {
  let sessionUrl: string | undefined;
  try {
    const url = new URL(record.source_session_url ?? "");
    if (["http:", "https:"].includes(url.protocol)) sessionUrl = url.href;
  } catch { /* Records without a session URL still retain their identifier. */ }
  return <section className="provenance" aria-label="Original session information">
    <div className="section-label">ORIGINATING SESSION <span>Preserved on every edit</span></div>
    <dl className="metadata-grid">
      <div><dt>Client</dt><dd>{clientLabel(record.source_client)}</dd></div>
      <div><dt>Model</dt><dd>{record.source_model || "Not recorded"}</dd></div>
      <div className="span-two"><dt>Session ID</dt><dd className="mono break-all">{record.source_session_id}</dd></div>
      {sessionUrl && <div className="span-two"><dt>Session link</dt><dd><a className="text-link" href={sessionUrl} target="_blank" rel="noopener noreferrer">Open original session <Icon name="external" size={14} /></a></dd></div>}
    </dl>
  </section>;
}

type EditDraft = {
  title: string; summary: string; prompt: string; status: HandoffStatus;
  repository_branch: string; verified_against: string; tags: string[]; metadata: string;
};

function draftFromRecord(record: Handoff): EditDraft {
  return {
    title: record.title, summary: record.summary, prompt: record.prompt, status: record.status,
    repository_branch: record.repository_branch ?? "", verified_against: record.verified_against ?? "",
    tags: [...record.tags], metadata: JSON.stringify(record.source_metadata, null, 2)
  };
}

function changedFields(draft: EditDraft, base: Handoff, pendingTag: string): HandoffPatch {
  let metadata: unknown;
  try { metadata = JSON.parse(draft.metadata); }
  catch { throw new Error("Extra metadata must be valid JSON. Your other edits have been kept."); }
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) throw new Error("Extra metadata must be a JSON object.");
  if (draft.verified_against.trim() && !/^[a-fA-F0-9]{7,64}$/.test(draft.verified_against.trim())) throw new Error("Verified commit must be a Git commit ID with 7–64 hexadecimal characters.");
  const tags = [...draft.tags];
  if (pendingTag.trim() && !tags.some((tag) => tag.toLowerCase() === pendingTag.trim().toLowerCase())) tags.push(pendingTag.trim());
  if (tags.length > 20) throw new Error("Use no more than 20 tags.");
  const candidate = {
    title: draft.title, summary: draft.summary, prompt: draft.prompt, status: draft.status,
    repository_branch: draft.repository_branch.trim() || null,
    verified_against: draft.verified_against.trim() || null,
    tags, source_metadata: metadata as Record<string, unknown>
  };
  const patch: HandoffPatch = { expected_version: base.version };
  for (const field of Object.keys(candidate) as Array<keyof typeof candidate>) {
    if (JSON.stringify(candidate[field]) !== JSON.stringify(base[field])) Object.assign(patch, { [field]: candidate[field] });
  }
  return patch;
}

function ErrorNotice({ message, children }: { message: string; children?: ReactNode }) {
  return <div className="error-notice" role="alert"><p>{message}</p>{children}</div>;
}

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState("");
  const [projectsRefresh, setProjectsRefresh] = useState(0);
  const [activeId, setActiveId] = useState("");
  const [projectDialog, setProjectDialog] = useState(false);
  const [projectSaving, setProjectSaving] = useState(false);
  const [newProjectError, setNewProjectError] = useState("");

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("open");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [results, setResults] = useState<Page<HandoffSummary> | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const [opened, setOpened] = useState<HandoffSummary | null>(null);
  const [record, setRecord] = useState<Handoff | null>(null);
  const [recordLoading, setRecordLoading] = useState(false);
  const [recordError, setRecordError] = useState("");
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [draft, setDraft] = useState<EditDraft | null>(null);
  const [tagInput, setTagInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [hasConflict, setHasConflict] = useState(false);
  const [latest, setLatest] = useState<Handoff | null>(null);
  const [latestLoading, setLatestLoading] = useState(false);
  const recordRequest = useRef(0);

  const [deleteTarget, setDeleteTarget] = useState<HandoffSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [deleteConflict, setDeleteConflict] = useState(false);
  const [copying, setCopying] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; error?: boolean } | null>(null);
  const project = projects.find((item) => item.id === activeId);

  useEffect(() => {
    const controller = new AbortController();
    setProjectsLoading(true);
    setProjectsError("");
    async function load() {
      const all: Project[] = [];
      let total = 1;
      while (all.length < total) {
        const page = await api<Page<Project>>(`/projects?limit=100&offset=${all.length}`, { signal: controller.signal });
        all.push(...page.items);
        total = page.total;
        if (!page.items.length) break;
      }
      if (controller.signal.aborted) return;
      all.sort((a, b) => a.name.localeCompare(b.name));
      setProjects(all);
      let saved = "";
      try { saved = localStorage.getItem("mnemonic.project") ?? ""; } catch { /* Storage can be disabled by the browser. */ }
      setActiveId((current) => all.some((item) => item.id === current) ? current : all.find((item) => item.id === saved)?.id ?? all[0]?.id ?? "");
    }
    load().catch((error) => { if (!controller.signal.aborted) setProjectsError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setProjectsLoading(false); });
    return () => controller.abort();
  }, [projectsRefresh]);

  useEffect(() => {
    if (!activeId) return;
    try { localStorage.setItem("mnemonic.project", activeId); } catch { /* Project selection still works without browser storage. */ }
  }, [activeId]);

  useEffect(() => {
    const timeout = setTimeout(() => { setSearch(query); setOffset(0); }, 300);
    return () => clearTimeout(timeout);
  }, [query]);

  useEffect(() => {
    if (!activeId) { setResults(null); setListLoading(false); return; }
    const controller = new AbortController();
    setListLoading(true);
    setListError("");
    const params = new URLSearchParams({ status, limit: String(PAGE_SIZE), offset: String(offset) });
    if (search.trim()) params.set("q", search.trim());
    api<Page<HandoffSummary>>(`/projects/${activeId}/handoffs?${params}`, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= page.total) { setOffset(Math.max(0, Math.floor((page.total - 1) / PAGE_SIZE) * PAGE_SIZE)); return; }
        setResults(page);
      })
      .catch((error) => { if (!controller.signal.aborted) setListError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [activeId, search, status, offset, refresh]);

  useEffect(() => {
    if (!notice || notice.error) return;
    const timer = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(null), 2500);
    return () => clearTimeout(timer);
  }, [copied]);

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && !target.isContentEditable && !document.querySelector("dialog[open]")) {
        event.preventDefault(); searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  function chooseProject(id: string) {
    setActiveId(id); setOffset(0); setQuery(""); setSearch(""); setStatus("open"); setResults(null);
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setProjectSaving(true); setNewProjectError("");
    try {
      const created = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), description: form.get("description") || "", ...(form.get("slug") ? { slug: form.get("slug") } : {}), ...(form.get("repository_url") ? { repository_url: form.get("repository_url") } : {}) })
      });
      setProjects((items) => [...items, created].sort((a, b) => a.name.localeCompare(b.name)));
      chooseProject(created.id); setProjectDialog(false); setNotice({ message: `“${created.name}” is ready for its first hand-off.` });
    } catch (error) { setNewProjectError(errorMessage(error)); }
    finally { setProjectSaving(false); }
  }

  async function openRecord(summary: HandoffSummary, editing = false) {
    const requestId = ++recordRequest.current;
    setOpened(summary); setRecord(null); setRecordError(""); setRecordLoading(true); setNotice(null);
    setMode(editing ? "edit" : "view"); setDraft(null); setTagInput(""); setEditError(""); setLatest(null); setHasConflict(false);
    try {
      const full = await api<Handoff>(handoffPath(summary.project_id, summary.id));
      if (recordRequest.current !== requestId) return;
      setRecord(full); setDraft(draftFromRecord(full));
    } catch (error) { if (recordRequest.current === requestId) setRecordError(errorMessage(error)); }
    finally { if (recordRequest.current === requestId) setRecordLoading(false); }
  }

  function closeRecord() {
    if (saving) return;
    const dirty = record && draft && (JSON.stringify(draft) !== JSON.stringify(draftFromRecord(record)) || tagInput.trim());
    if (mode === "edit" && dirty && !window.confirm("Discard your unsaved edits?")) return;
    ++recordRequest.current;
    setOpened(null); setRecord(null); setDraft(null); setLatest(null);
  }

  async function copyPrompt(summary: HandoffSummary, loaded?: Handoff) {
    if (copying) return;
    setCopying(summary.id);
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable. Open the full prompt and select its text to copy it manually.");
      const full = loaded ?? await api<Handoff>(handoffPath(summary.project_id, summary.id));
      await navigator.clipboard.writeText(full.prompt);
      setCopied(summary.id); setNotice({ message: "Full prompt copied. Ready for a fresh session." });
    } catch (error) {
      setNotice({ message: error instanceof DOMException && error.name === "NotAllowedError" ? "The browser blocked clipboard access. Allow it for this page, or select and copy the full prompt manually." : errorMessage(error), error: true });
    } finally { setCopying(null); }
  }

  async function copyProjectId() {
    if (!project) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error(`Clipboard access is unavailable. Project ID: ${project.id}`);
      await navigator.clipboard.writeText(project.id);
      setNotice({ message: `Project ID copied: ${project.id}` });
    } catch (error) { setNotice({ message: errorMessage(error), error: true }); }
  }

  async function saveEdits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record || !draft) return;
    setEditError("");
    let patch: HandoffPatch;
    try { patch = changedFields(draft, record, tagInput); }
    catch (error) { setEditError(errorMessage(error)); return; }
    if (Object.keys(patch).length === 1) { setMode("view"); return; }
    setSaving(true);
    try {
      const saved = await api<Handoff>(handoffPath(record.project_id, record.id), { method: "PATCH", body: JSON.stringify(patch) });
      setRecord(saved); setDraft(draftFromRecord(saved)); setMode("view"); setTagInput(""); setLatest(null); setHasConflict(false);
      setRefresh((value) => value + 1); setNotice({ message: "Hand-off saved. Original session information preserved." });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setHasConflict(true); setLatest(null);
        setEditError("This hand-off changed after you opened it. Your edits are still here. Compare the current version before saving again.");
      } else setEditError(errorMessage(error));
    } finally { setSaving(false); }
  }

  async function loadLatest() {
    if (!record) return;
    setLatestLoading(true);
    try { setLatest(await api<Handoff>(handoffPath(record.project_id, record.id))); }
    catch (error) { setEditError(errorMessage(error)); }
    finally { setLatestLoading(false); }
  }

  function mergeLatest() {
    if (!record || !draft || !latest) return;
    try {
      const { expected_version, ...changes } = changedFields(draft, record, tagInput);
      void expected_version;
      setDraft(draftFromRecord({ ...latest, ...changes })); setRecord(latest); setTagInput("");
      setLatest(null); setHasConflict(false); setEditError("");
    } catch (error) { setEditError(errorMessage(error)); }
  }

  function requestDelete(summary: HandoffSummary) {
    setDeleteTarget(summary); setDeleteError(""); setDeleteConflict(false);
  }

  async function deleteHandoff() {
    if (!deleteTarget) return;
    setDeleting(true); setDeleteError("");
    try {
      await api<void>(`${handoffPath(deleteTarget.project_id, deleteTarget.id)}?expected_version=${deleteTarget.version}`, { method: "DELETE" });
      if (opened?.id === deleteTarget.id) { setOpened(null); setRecord(null); }
      setDeleteTarget(null); setRefresh((value) => value + 1); setNotice({ message: "Hand-off removed from the library." });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) { setDeleteConflict(true); setDeleteError("This hand-off changed since it was listed. Load the current version and review it before deleting."); }
      else setDeleteError(errorMessage(error));
    } finally { setDeleting(false); }
  }

  async function refreshDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const full = await api<Handoff>(handoffPath(deleteTarget.project_id, deleteTarget.id));
      setDeleteTarget(full); setDeleteConflict(false); setDeleteError(""); setRefresh((value) => value + 1);
    } catch (error) { setDeleteError(errorMessage(error)); }
    finally { setDeleting(false); }
  }

  function addTag() {
    if (!draft || !tagInput.trim() || draft.tags.length >= 20) return;
    const tag = tagInput.trim();
    if (!draft.tags.some((existing) => existing.toLowerCase() === tag.toLowerCase())) setDraft({ ...draft, tags: [...draft.tags, tag] });
    setTagInput("");
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to hand-offs</a>
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Mnemonic home"><Logo /><span>mnemonic<span className="brand-period">.</span></span></a>
      <div className="workspace-picker">
        <label className="section-label" htmlFor="project-select">YOUR WORKSPACE</label>
        <div className="select-wrap"><select id="project-select" value={activeId} disabled={projectsLoading || !projects.length} onChange={(event) => chooseProject(event.target.value)} aria-label="Select project">
          {!projects.length && <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>}
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><span className="select-chevron" aria-hidden="true">⌄</span></div>
        <button className="new-project-button" type="button" disabled={projectsLoading} onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={15} />New project</button>
        {project && <button className="copy-project-button" type="button" title={`Project ID: ${project.id}`} onClick={() => void copyProjectId()}><Icon name="copy" size={13} />Copy project ID for your agent</button>}
      </div>
      <nav aria-label="Workspace navigation"><a className="nav-item active" href="#main-content" aria-current="page"><Icon name="library" /><span>Hand-off library</span><Icon name="arrow" size={15} /></a></nav>
      <div className="sidebar-note"><div className="note-art" aria-hidden="true"><span /><span /><span /></div><h2>Keep the context.<br />Pick up the work.</h2><p>A good next step deserves a longer memory than a session.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /><span>Local workspace</span><span className="mvp-label">MVP</span></div>
    </aside>

    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><span>{project?.name || "Getting started"}</span></div><span className="topbar-note"><span className="small-mark" aria-hidden="true">m.</span>Context worth keeping</span></header>
      <div className="page-content">
        <section className="page-heading"><div><div className="eyebrow">A PLACE TO PICK UP WHERE YOU LEFT OFF</div><h1>Hand-off library<span>.</span></h1><p>{project?.description || "Complete prompts. Original context. Ready for your next session."}</p></div><button className="button button-secondary refresh-button" type="button" aria-label="Refresh workspace" title="Refresh workspace" disabled={projectsLoading || listLoading} onClick={() => { setProjectsRefresh((value) => value + 1); setRefresh((value) => value + 1); }}><Icon name="refresh" size={16} /><span>Refresh</span></button></section>

        {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> : projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> : !projects.length ? <section className="empty-state onboarding"><div className="empty-art"><Icon name="library" size={34} /><span /></div><div className="eyebrow">YOUR NEXT SESSION WILL THANK YOU</div><h2>Make room for what comes next.</h2><p>Create a project to give your hand-off prompts a home.<br />Then connect your agent and start keeping the context that matters.</p><button className="button button-primary" onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={17} />Create your first project</button><div className="onboarding-footnote">One project, many sessions. Every prompt keeps its origin.</div></section> : <>
          <section className="library-controls" aria-label="Find hand-offs">
            <div className="search-field"><Icon name="search" size={20} /><input ref={searchRef} type="search" value={query} maxLength={500} aria-label="Search hand-offs" placeholder="Search prompts, context, or session IDs…" onChange={(event) => setQuery(event.target.value)} />{query ? <button className="icon-button" type="button" aria-label="Clear search" onClick={() => setQuery("")}><Icon name="close" size={16} /></button> : <kbd aria-hidden="true">/</kbd>}</div>
            <div className="filter-row"><div className="status-filters" role="group" aria-label="Filter by status">{filters.map((filter) => <button key={filter} className={`filter-button ${status === filter ? "selected" : ""}`} aria-pressed={status === filter} onClick={() => { setStatus(filter); setOffset(0); }}>{filter === "open" && <span className="filter-dot" />}{statusLabels[filter]}</button>)}</div><span className="result-count" role="status">{listLoading || query !== search ? "Finding hand-offs…" : results ? `${results.total} hand-off${results.total === 1 ? "" : "s"}` : ""}</span></div>
          </section>

          {listError ? <ErrorNotice message={listError}><button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Try again</button></ErrorNotice> : listLoading ? <div className="card-skeletons" role="status" aria-label="Loading hand-offs">{[1, 2, 3].map((item) => <div className="card-skeleton" key={item}><span /><span /><span /></div>)}<span className="sr-only">Loading hand-offs…</span></div> : !results?.items.length ? <section className="empty-state"><div className="empty-art"><Icon name={search ? "search" : "box"} size={31} /><span /></div><h2>{search ? "No matches this time." : status === "open" ? "Your next chapter starts here." : `No ${statusLabels[status].toLowerCase()} hand-offs.`}</h2><p>{search ? "Try another search, or look across all statuses." : status === "open" ? "Ask your agent to save a complete hand-off to this project. It will be here when you’re ready to continue." : "Saved prompts with this status will appear here."}</p>{search || status !== "open" ? <button className="button button-secondary" onClick={() => { setQuery(""); setSearch(""); setStatus(search ? "all" : "open"); setOffset(0); }}>{search ? "Clear search & show all" : "Show open hand-offs"}</button> : <div className="agent-hint"><span>TRY ASKING YOUR CONNECTED AGENT</span><p>“Save a complete hand-off in Mnemonic for the {project?.name} project.”</p></div>}</section> : <section className="handoff-list" aria-label="Stored hand-off prompts">
            {results.items.map((item) => <article className="handoff-card" key={item.id}>
              <div className="card-topline"><StatusBadge status={item.status} /><span className="card-source">{clientLabel(item.source_client)}<span>·</span><time dateTime={item.updated_at} title={formatDate(item.updated_at, true)}>Updated {formatDate(item.updated_at)}</time></span><span className="card-version">v{item.version}</span></div>
              <button className="card-title" type="button" onClick={() => void openRecord(item)}><h2>{item.title}</h2><Icon name="arrow" size={18} /></button>
              <p className="card-summary">{item.summary}</p>
              <div className="card-footer"><div className="card-context">{item.tags.slice(0, 3).map((tag) => <span className="tag" key={tag}>{tag}</span>)}{item.tags.length > 3 && <span className="extra-tags" title={item.tags.slice(3).join(", ")}>+{item.tags.length - 3}</span>}<span className="session-snippet" title={`Session: ${item.source_session_id}`}>session <span>{item.source_session_id}</span></span></div><div className="card-actions"><button className="icon-button" aria-label={`Edit ${item.title}`} title="Edit hand-off" onClick={() => void openRecord(item, true)}><Icon name="edit" size={17} /></button><button className="icon-button danger-hover" aria-label={`Delete ${item.title}`} title="Delete hand-off" onClick={() => requestDelete(item)}><Icon name="trash" size={17} /></button><span className="action-divider" /><button className={`button copy-button ${copied === item.id ? "is-copied" : ""}`} aria-label={`Copy prompt: ${item.title}`} disabled={copying !== null} onClick={() => void copyPrompt(item)}><Icon name={copied === item.id ? "check" : "copy"} size={16} />{copying === item.id ? "Copying…" : copied === item.id ? "Copied" : "Copy prompt"}</button></div></div>
            </article>)}
          </section>}
          {!listLoading && !listError && results && results.total > 0 && <nav className="pagination" aria-label="Hand-off result pages"><span>Showing {offset + 1}–{Math.min(offset + results.items.length, results.total)} of {results.total}</span><div><button className="button button-secondary" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}><Icon name="back" size={15} />Previous</button><button className="button button-secondary" disabled={offset + results.items.length >= results.total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>Next<Icon name="arrow" size={15} /></button></div></nav>}
          <footer className="library-footer"><Icon name="box" size={15} /><span>Agent-authored context, kept across sessions. Review the source before acting.</span></footer>
        </>}
      </div>
    </main>

    {notice && <div className={`toast ${notice.error ? "toast-error" : ""}`} role={notice.error ? "alert" : "status"}><Icon name={notice.error ? "close" : "check"} size={18} /><span>{notice.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><Icon name="close" size={16} /></button></div>}

    {projectDialog && <Dialog title="A new place for your next steps" onClose={() => { if (!projectSaving) setProjectDialog(false); }} busy={projectSaving}>
      <p className="dialog-intro">Projects keep related prompts together, across agents and sessions.</p>
      <form className="form-stack" onSubmit={(event) => void createProject(event)}>
        <label className="field">Project name<input name="name" required maxLength={120} autoFocus placeholder="e.g. My application" /></label>
        <label className="field">Project slug <span className="optional">Optional</span><input name="slug" maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="my-application" /><span className="field-hint">A stable name for your agents. Generated from the project name if blank.</span></label>
        <label className="field">Description <span className="optional">Optional</span><textarea name="description" rows={3} maxLength={4000} placeholder="What is this project about?" /></label>
        <label className="field">Repository URL <span className="optional">Optional</span><input name="repository_url" type="url" maxLength={2000} placeholder="https://github.com/you/project" /></label>
        {newProjectError && <ErrorNotice message={newProjectError} />}
        <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={projectSaving} onClick={() => setProjectDialog(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={projectSaving}>{projectSaving ? "Creating…" : "Create project"}<Icon name="arrow" size={16} /></button></div>
      </form>
    </Dialog>}

    {opened && <Dialog title={mode === "edit" ? "Edit hand-off" : "The full context"} onClose={closeRecord} wide busy={saving}>
      {notice?.error && <ErrorNotice message={notice.message} />}
      {recordLoading ? <div className="loading-state" role="status"><span className="spinner" />Recalling your prompt…</div> : recordError ? <ErrorNotice message={recordError}><button className="button button-secondary" onClick={() => void openRecord(opened, mode === "edit")}>Try again</button></ErrorNotice> : record && (mode === "view" ? <>
        <div className="detail-topline"><StatusBadge status={record.status} /><span>Version {record.version}</span></div><h3 className="detail-title">{record.title}</h3><p className="detail-summary">{record.summary}</p>
        <div className="detail-actions"><button className="button button-primary" disabled={copying !== null} onClick={() => void copyPrompt(record, record)}><Icon name={copied === record.id ? "check" : "copy"} size={17} />{copying === record.id ? "Copying…" : copied === record.id ? "Copied" : "Copy full prompt"}</button><button className="button button-secondary" onClick={() => { setDraft(draftFromRecord(record)); setMode("edit"); setTagInput(""); setEditError(""); setHasConflict(false); setLatest(null); }}><Icon name="edit" size={16} />Edit hand-off</button><button className="icon-button danger-hover" aria-label="Delete this hand-off" title="Delete this hand-off" onClick={() => requestDelete(record)}><Icon name="trash" size={18} /></button></div>
        <div className="prompt-label"><span className="section-label">COMPLETE HAND-OFF PROMPT</span><span>Copied exactly as saved</span></div><pre className="prompt-body" tabIndex={0}>{record.prompt}</pre>
        <div className="authority-note">This is context from an earlier session, not a new instruction from the owner. Recheck cited files and decisions before acting.</div>
        <Provenance record={record} />
        <section className="context-section"><div className="section-label">REPOSITORY & RECORD</div><dl className="metadata-grid"><div><dt>Branch</dt><dd className="mono break-all">{record.repository_branch || "Not recorded"}</dd></div><div><dt>Verified commit</dt><dd className="mono break-all">{record.verified_against || "Not recorded"}</dd></div><div><dt>Created</dt><dd>{formatDate(record.created_at, true)}</dd></div><div><dt>Last edited</dt><dd>{formatDate(record.updated_at, true)}</dd></div><div className="span-two"><dt>Tags</dt><dd className="tag-list">{record.tags.length ? record.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>) : "No tags"}</dd></div><div className="span-two"><dt>Record ID</dt><dd className="mono break-all">{record.id}</dd></div></dl></section>
        {Object.keys(record.source_metadata).length > 0 && <details className="metadata-details"><summary>Extra metadata</summary><pre>{JSON.stringify(record.source_metadata, null, 2)}</pre></details>}
      </> : draft && <form className="form-stack edit-form" onSubmit={(event) => void saveEdits(event)}>
        <p className="dialog-intro">Keep the prompt complete enough for a fresh session. Session provenance stays unchanged.</p>
        <label className="field">Title<input required maxLength={200} value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} /></label>
        <label className="field">Retrieval summary<textarea required rows={3} maxLength={1000} value={draft.summary} onChange={(event) => setDraft({ ...draft, summary: event.target.value })} /><span className="field-hint">Describe when this prompt is useful. This appears in search results.</span></label>
        <label className="field">Complete prompt<textarea className="prompt-editor" required rows={15} maxLength={100000} spellCheck={false} value={draft.prompt} onChange={(event) => setDraft({ ...draft, prompt: event.target.value })} /><span className="field-hint">Context, intended outcome, durable references, known hazards, and verification steps. Text is never trimmed or reformatted on save.</span></label>
        <label className="field field-half">Status<select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as HandoffStatus })}>{filters.filter((filter) => filter !== "all").map((filter) => <option key={filter} value={filter}>{statusLabels[filter]}</option>)}</select><span className="field-hint">Only open hand-offs appear in default agent searches. Promotion records a decision; it does not create an external issue.</span></label>
        <div className="field"><label htmlFor="tag-input">Tags <span className="optional">{draft.tags.length}/20</span></label><div className="tag-editor">{draft.tags.map((tag, index) => <span className="tag removable-tag" key={`${tag}-${index}`}>{tag}<button type="button" aria-label={`Remove tag ${tag}`} onClick={() => setDraft({ ...draft, tags: draft.tags.filter((_, tagIndex) => tagIndex !== index) })}><Icon name="close" size={12} /></button></span>)}</div><div className="tag-input-row"><input id="tag-input" value={tagInput} maxLength={50} placeholder="Add a tag" disabled={draft.tags.length >= 20} onChange={(event) => setTagInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTag(); } }} /><button type="button" className="button button-secondary" disabled={!tagInput.trim() || draft.tags.length >= 20} onClick={addTag}><Icon name="plus" size={15} />Add</button></div></div>
        <details className="edit-context"><summary>Repository context & extra metadata</summary><div className="form-stack"><label className="field">Repository branch<input maxLength={200} value={draft.repository_branch} onChange={(event) => setDraft({ ...draft, repository_branch: event.target.value })} /></label><label className="field">Verified commit<input className="mono" maxLength={64} value={draft.verified_against} onChange={(event) => setDraft({ ...draft, verified_against: event.target.value })} /><span className="field-hint">The commit the citations were checked against. A recorded commit is not a guarantee of current freshness.</span></label><label className="field">Extra metadata <span className="optional">JSON object</span><textarea className="mono" rows={6} spellCheck={false} value={draft.metadata} onChange={(event) => setDraft({ ...draft, metadata: event.target.value })} /></label></div></details>
        <Provenance record={record} />
        {editError && <ErrorNotice message={editError}>{hasConflict && <button type="button" className="button button-secondary" disabled={latestLoading} onClick={() => void loadLatest()}>{latestLoading ? "Loading…" : "Compare current version"}</button>}</ErrorNotice>}
        {latest && <section className="conflict-panel"><h3>Current saved version · v{latest.version}</h3><p>Compare the saved values below with your edits above. Continuing keeps the fields you changed and takes untouched fields from this version. Nothing is saved until you click Save changes.</p><pre tabIndex={0}>{JSON.stringify({ title: latest.title, summary: latest.summary, prompt: latest.prompt, status: latest.status, repository_branch: latest.repository_branch, verified_against: latest.verified_against, tags: latest.tags, source_metadata: latest.source_metadata }, null, 2)}</pre><button type="button" className="button button-secondary" onClick={mergeLatest}>Keep my edits on this version</button></section>}
        <div className="dialog-actions sticky-actions"><span className="version-note">Editing version {record.version}</span><button type="button" className="button button-secondary" disabled={saving} onClick={closeRecord}>Cancel</button><button type="submit" className="button button-primary" disabled={saving || hasConflict}>{saving ? "Saving…" : "Save changes"}<Icon name="check" size={17} /></button></div>
      </form>)}
    </Dialog>}

    {deleteTarget && <Dialog title="Delete this hand-off?" onClose={() => { if (!deleting) setDeleteTarget(null); }} busy={deleting}>
      <p className="dialog-intro">This removes the prompt from this project’s library and from agent search results.</p><div className="delete-preview"><StatusBadge status={deleteTarget.status} /><h3>{deleteTarget.title}</h3><p>{deleteTarget.summary}</p><span>Version {deleteTarget.version} · Updated {formatDate(deleteTarget.updated_at, true)}</span></div>
      {deleteError && <ErrorNotice message={deleteError}>{deleteConflict && <button className="button button-secondary" disabled={deleting} onClick={() => void refreshDelete()}>Load current version</button>}</ErrorNotice>}
      <div className="dialog-actions"><button className="button button-secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>Keep hand-off</button><button className="button button-danger" disabled={deleting || deleteConflict} onClick={() => void deleteHandoff()}><Icon name="trash" size={16} />{deleting ? "Working…" : "Delete hand-off"}</button></div>
    </Dialog>}
  </div>;
}
