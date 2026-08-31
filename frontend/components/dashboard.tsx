"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CHECKPOINT_PAGE_SIZE } from "@/components/checkpoint-timeline";
import WorkItemDetail from "@/components/work-item-detail";
import WorkItemList, { WORK_PAGE_SIZE } from "@/components/work-item-list";
import { StatusBadge, formatDate } from "@/components/work-item-card";
import { draftFromWork, type WorkEditDraft } from "@/components/work-item-editor";
import { api, ApiError, errorMessage, workItemPath } from "@/lib/api";
import type {
  Checkpoint,
  CheckpointInput,
  CheckpointKind,
  CompletionResult,
  DeletionResult,
  Page,
  Project,
  StatusFilter,
  WorkContext,
  WorkCreation,
  WorkItem,
  WorkPatch,
  WorkSummary
} from "@/lib/types";
import { normalizedTags } from "@/lib/work-item-view";
import { workSearchParams } from "@/lib/work-item-search";

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  plus: "M12 5v14M5 12h14",
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  refresh: "M20 7V2m0 5h-5M4 17v5m0-5h5M4 8a8 8 0 0 1 13-4l3 3M4 17l3 3a8 8 0 0 0 13-4",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

function Logo() {
  return <svg className="logo-mark" width="34" height="34" viewBox="0 0 34 34" fill="none" aria-hidden="true"><rect width="34" height="34" rx="10" fill="currentColor" /><path d="M9 24V10h4l4 7 4-7h4v14h-4v-8l-4 6-4-6v8H9Z" fill="#f9f8f3" /></svg>;
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

function ErrorNotice({ message, children }: { message: string; children?: ReactNode }) {
  return <div className="error-notice" role="alert"><p>{message}</p>{children}</div>;
}

function dashboardSessionId() {
  const key = "mnemonic.dashboard-session";
  try {
    const saved = sessionStorage.getItem(key);
    if (saved) return saved;
    const created = crypto.randomUUID();
    sessionStorage.setItem(key, created);
    return created;
  } catch {
    return `dashboard-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

function checkpointPayload(
  prompt: string,
  branch = "",
  commit = "",
  tagText = ""
): CheckpointInput {
  const verified = commit.trim();
  if (verified && !/^[a-fA-F0-9]{7,64}$/.test(verified)) {
    throw new Error("Verified commit must be a Git commit ID with 7–64 hexadecimal characters.");
  }
  return {
    prompt,
    source_client: "dashboard",
    source_session_id: dashboardSessionId(),
    source_model: null,
    repository_branch: branch.trim() || null,
    verified_against: verified || null,
    tags: normalizedTags(tagText),
    source_metadata: {}
  };
}

function summaryWithContext(base: WorkSummary, context: WorkContext): WorkSummary {
  return {
    ...base,
    work_item: context.work_item,
    checkpoint_count: context.checkpoint_total,
    current_context: context.current_context,
    readiness: context.readiness
  };
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
  const [semantic, setSemantic] = useState(false);
  const [status, setStatus] = useState<StatusFilter>("open");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [results, setResults] = useState<Page<WorkSummary> | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const [workDialog, setWorkDialog] = useState(false);
  const [workSaving, setWorkSaving] = useState(false);
  const [newWorkError, setNewWorkError] = useState("");

  const [opened, setOpened] = useState<WorkSummary | null>(null);
  const [context, setContext] = useState<WorkContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editDraft, setEditDraft] = useState<WorkEditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const recordRequest = useRef(0);

  const [checkpointPage, setCheckpointPage] = useState<Page<Checkpoint> | null>(null);
  const [checkpointOffset, setCheckpointOffset] = useState(0);
  const [checkpointLoading, setCheckpointLoading] = useState(false);
  const [checkpointLoadError, setCheckpointLoadError] = useState("");
  const [checkpointActionError, setCheckpointActionError] = useState("");
  const [checkpointRefresh, setCheckpointRefresh] = useState(0);
  const [checkpointKind, setCheckpointKind] = useState<Exclude<CheckpointKind, "completion">>("progress");
  const [checkpointBody, setCheckpointBody] = useState("");
  const [checkpointBranch, setCheckpointBranch] = useState("");
  const [checkpointCommit, setCheckpointCommit] = useState("");
  const [checkpointTags, setCheckpointTags] = useState("");
  const [checkpointSaving, setCheckpointSaving] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<WorkItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
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
      try { saved = localStorage.getItem("mnemonic.project") ?? ""; } catch { /* optional */ }
      setActiveId((current) => all.some((item) => item.id === current) ? current : all.find((item) => item.id === saved)?.id ?? all[0]?.id ?? "");
    }
    load().catch((error) => { if (!controller.signal.aborted) setProjectsError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setProjectsLoading(false); });
    return () => controller.abort();
  }, [projectsRefresh]);

  useEffect(() => {
    if (!activeId) return;
    try { localStorage.setItem("mnemonic.project", activeId); } catch { /* optional */ }
  }, [activeId]);

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(query); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!activeId) { setResults(null); return; }
    const controller = new AbortController();
    setListLoading(true);
    setListError("");
    const params = workSearchParams({ status, limit: WORK_PAGE_SIZE, offset, query: search, semantic });
    api<Page<WorkSummary>>(`${workItemPath(activeId)}?${params}`, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= page.total) {
          setOffset(Math.max(0, Math.floor((page.total - 1) / WORK_PAGE_SIZE) * WORK_PAGE_SIZE));
          return;
        }
        setResults(page);
      })
      .catch((error) => { if (!controller.signal.aborted) setListError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [activeId, offset, refresh, search, semantic, status]);

  useEffect(() => {
    if (!opened) { setCheckpointPage(null); return; }
    const controller = new AbortController();
    setCheckpointLoading(true);
    setCheckpointLoadError("");
    const base = workItemPath(opened.work_item.project_id, opened.work_item.id);
    api<Page<Checkpoint>>(`${base}/checkpoints?order=newest&limit=${CHECKPOINT_PAGE_SIZE}&offset=${checkpointOffset}`, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        if (checkpointOffset > 0 && checkpointOffset >= page.total) {
          setCheckpointOffset(Math.max(0, Math.floor((page.total - 1) / CHECKPOINT_PAGE_SIZE) * CHECKPOINT_PAGE_SIZE));
          return;
        }
        setCheckpointPage(page);
      })
      .catch((error) => { if (!controller.signal.aborted) setCheckpointLoadError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setCheckpointLoading(false); });
    return () => controller.abort();
  }, [opened, checkpointOffset, checkpointRefresh]);

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
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);

  function chooseProject(id: string) {
    setActiveId(id);
    setOffset(0);
    setQuery("");
    setSearch("");
    setSemantic(false);
    setStatus("open");
    setResults(null);
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setProjectSaving(true);
    setNewProjectError("");
    try {
      const created = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("name"),
          description: form.get("description") || "",
          ...(form.get("slug") ? { slug: form.get("slug") } : {}),
          ...(form.get("repository_url") ? { repository_url: form.get("repository_url") } : {})
        })
      });
      setProjects((items) => [...items, created].sort((a, b) => a.name.localeCompare(b.name)));
      chooseProject(created.id);
      setProjectDialog(false);
      setNotice({ message: `“${created.name}” is ready for durable work.` });
    } catch (error) {
      setNewProjectError(errorMessage(error));
    } finally {
      setProjectSaving(false);
    }
  }

  async function createWork(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!project) return;
    const form = new FormData(event.currentTarget);
    setWorkSaving(true);
    setNewWorkError("");
    try {
      const prompt = String(form.get("prompt") ?? "");
      const initialCheckpoint = checkpointPayload(
        prompt,
        String(form.get("repository_branch") ?? ""),
        String(form.get("verified_against") ?? ""),
        String(form.get("tags") ?? "")
      );
      const created = await api<WorkCreation>(workItemPath(project.id), {
        method: "POST",
        body: JSON.stringify({
          title: form.get("title"),
          summary: form.get("summary"),
          priority: Number(form.get("priority") ?? 0),
          status: "open",
          initial_checkpoint: initialCheckpoint
        })
      });
      setWorkDialog(false);
      setStatus("open");
      setOffset(0);
      setRefresh((value) => value + 1);
      setNotice({ message: `“${created.work_item.title}” now has its first immutable checkpoint.` });
    } catch (error) {
      setNewWorkError(errorMessage(error));
    } finally {
      setWorkSaving(false);
    }
  }

  async function loadContext(summary: WorkSummary, requestId = recordRequest.current) {
    setContextLoading(true);
    setContextError("");
    try {
      const full = await api<WorkContext>(`${workItemPath(summary.work_item.project_id, summary.work_item.id)}/context?recent_limit=5`);
      if (recordRequest.current !== requestId) return;
      setContext(full);
      setOpened((current) => current ? summaryWithContext(current, full) : current);
      setEditDraft(draftFromWork(full.work_item));
    } catch (error) {
      if (recordRequest.current === requestId) setContextError(errorMessage(error));
    } finally {
      if (recordRequest.current === requestId) setContextLoading(false);
    }
  }

  function openWork(summary: WorkSummary, editing = false) {
    const requestId = ++recordRequest.current;
    setOpened(summary);
    setContext(null);
    setContextError("");
    setMode(editing ? "edit" : "view");
    setEditDraft(draftFromWork(summary.work_item));
    setEditError("");
    setConflict(null);
    setCheckpointOffset(0);
    setCheckpointPage(null);
    setCheckpointBody("");
    setCheckpointBranch("");
    setCheckpointCommit("");
    setCheckpointTags("");
    setCheckpointKind("progress");
    setCheckpointActionError("");
    void loadContext(summary, requestId);
  }

  function closeWork() {
    if (editSaving || checkpointSaving) return;
    if (checkpointBody.trim() && !window.confirm("Discard your unsaved checkpoint?")) return;
    if (mode === "edit" && context && editDraft && JSON.stringify(editDraft) !== JSON.stringify(draftFromWork(context.work_item)) && !window.confirm("Discard your unsaved work-item edits?")) return;
    ++recordRequest.current;
    setOpened(null);
    setContext(null);
    setCheckpointPage(null);
    setCheckpointBody("");
  }

  async function reloadOpenContext() {
    if (!opened) return;
    await loadContext(opened);
  }

  async function saveWorkEdits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context || !editDraft) return;
    const base = context.work_item;
    const patch: WorkPatch = { expected_version: base.version };
    if (editDraft.title !== base.title) patch.title = editDraft.title;
    if (editDraft.summary !== base.summary) patch.summary = editDraft.summary;
    if (editDraft.priority !== base.priority) patch.priority = editDraft.priority;
    if (editDraft.status !== base.status && editDraft.status !== "done") patch.status = editDraft.status;
    if (Object.keys(patch).length === 1) { setMode("view"); return; }
    setEditSaving(true);
    setEditError("");
    try {
      const saved = await api<WorkItem>(workItemPath(base.project_id, base.id), { method: "PATCH", body: JSON.stringify(patch) });
      setContext((value) => value ? { ...value, work_item: saved, readiness: { ...value.readiness, lifecycle_status: saved.status, is_terminal: saved.status !== "open", is_ready: saved.status === "open", display_state: saved.status === "open" ? "ready" : saved.status } } : value);
      setOpened((value) => value ? { ...value, work_item: saved } : value);
      setEditDraft(draftFromWork(saved));
      setConflict(null);
      setMode("view");
      setRefresh((value) => value + 1);
      setNotice({ message: "Work item saved. Checkpoint history was not changed." });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 409 || error.code === "version_conflict")) {
        setEditError("This work item changed after you opened it. Your edits are still here.");
      } else {
        setEditError(errorMessage(error));
      }
    } finally {
      setEditSaving(false);
    }
  }

  async function loadLatestWork() {
    if (!context) return;
    setEditError("");
    try {
      const latest = await api<WorkItem>(workItemPath(context.work_item.project_id, context.work_item.id));
      setConflict(latest);
    } catch (error) {
      setEditError(errorMessage(error));
    }
  }

  function useCurrentVersion() {
    if (!conflict) return;
    setContext((value) => value ? { ...value, work_item: conflict } : value);
    setOpened((value) => value ? { ...value, work_item: conflict } : value);
    setConflict(null);
    setEditError("");
  }

  async function saveCheckpoint(complete: boolean) {
    if (!context || !checkpointBody.trim() || checkpointSaving) return;
    setCheckpointSaving(true);
    setCheckpointActionError("");
    try {
      const checkpoint = checkpointPayload(checkpointBody, checkpointBranch, checkpointCommit, checkpointTags);
      const base = workItemPath(context.work_item.project_id, context.work_item.id);
      if (complete) {
        const result = await api<CompletionResult>(`${base}/complete`, {
          method: "POST",
          body: JSON.stringify({ expected_version: context.work_item.version, checkpoint })
        });
        setNotice({ message: "Completion checkpoint recorded and work marked done." });
        setContext((value) => value ? { ...value, work_item: result.work_item, current_context: value.current_context, checkpoint_total: value.checkpoint_total + 1, readiness: { ...value.readiness, lifecycle_status: "done", is_terminal: true, is_ready: false, display_state: "done" } } : value);
      } else {
        await api<Checkpoint>(`${base}/checkpoints`, {
          method: "POST",
          body: JSON.stringify({ kind: checkpointKind, ...checkpoint })
        });
        setNotice({ message: checkpointKind === "context" ? "New current context recorded." : "Progress checkpoint recorded." });
      }
      setCheckpointBody("");
      setCheckpointBranch("");
      setCheckpointCommit("");
      setCheckpointTags("");
      setCheckpointOffset(0);
      setCheckpointRefresh((value) => value + 1);
      setRefresh((value) => value + 1);
      await reloadOpenContext();
    } catch (error) {
      if (complete && error instanceof ApiError && (error.status === 409 || error.code === "version_conflict")) {
        setCheckpointActionError("This work item changed before completion. Your summary is still here; the current version has been reloaded for review.");
        await reloadOpenContext();
      } else {
        setCheckpointActionError(errorMessage(error));
      }
    } finally {
      setCheckpointSaving(false);
    }
  }

  async function deleteWork() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await api<DeletionResult>(`${workItemPath(deleteTarget.project_id, deleteTarget.id)}/delete`, {
        method: "POST",
        body: JSON.stringify({ expected_version: deleteTarget.version })
      });
      if (opened?.work_item.id === deleteTarget.id) {
        setOpened(null);
        setContext(null);
      }
      setDeleteTarget(null);
      setRefresh((value) => value + 1);
      setNotice({ message: "Work item removed from ordinary project views. Its history remains recoverable." });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 409 || error.code === "version_conflict")) {
        try {
          const latest = await api<WorkItem>(workItemPath(deleteTarget.project_id, deleteTarget.id));
          setDeleteTarget(latest);
          setDeleteError(`This work item changed. Deletion was not retried; review the current version ${latest.version} before trying again.`);
        } catch (reloadError) {
          setDeleteError(`The work item changed, and its current version could not be loaded. ${errorMessage(reloadError)}`);
        }
      } else {
        setDeleteError(errorMessage(error));
      }
    } finally {
      setDeleting(false);
    }
  }

  async function copyText(value: string, key: string, success: string) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable.");
      await navigator.clipboard.writeText(value);
      setCopied(key);
      setNotice({ message: success });
    } catch (error) {
      setNotice({ message: errorMessage(error), error: true });
    }
  }

  async function copyProjectId() {
    if (project) await copyText(project.id, "project", `Project ID copied: ${project.id}`);
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to work items</a>
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Mnemonic home"><Logo /><span>mnemonic<span className="brand-period">.</span></span></a>
      <div className="workspace-picker">
        <label className="section-label" htmlFor="project-select">YOUR WORKSPACE</label>
        <div className="select-wrap"><select id="project-select" value={activeId} disabled={projectsLoading || !projects.length} onChange={(event) => chooseProject(event.target.value)}>
          {!projects.length && <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>}
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><span className="select-chevron" aria-hidden="true">⌄</span></div>
        <button className="new-project-button" type="button" disabled={projectsLoading} onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={15} />New project</button>
        {project && <button className="copy-project-button" type="button" title={`Project ID: ${project.id}`} onClick={() => void copyProjectId()}><Icon name="copy" size={13} />Copy project ID for your agent</button>}
      </div>
      <nav aria-label="Workspace navigation"><a className="nav-item active" href="#main-content" aria-current="page"><Icon name="library" /><span>Work library</span><Icon name="arrow" size={15} /></a></nav>
      <div className="sidebar-note"><div className="note-art" aria-hidden="true"><span /><span /><span /></div><h2>Keep the objective.<br />Pass on the context.</h2><p>Durable work survives while sessions leave immutable checkpoints.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /><span>Local workspace</span><span className="mvp-label">WORK GRAPH</span></div>
    </aside>

    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><span>{project?.name || "Getting started"}</span></div><span className="topbar-note"><span className="small-mark">m.</span>Context worth keeping</span></header>
      <div className="page-content">
        <section className="page-heading"><div><div className="eyebrow">DURABLE WORK FOR TEMPORARY SESSIONS</div><h1>Work library<span>.</span></h1><p>{project?.description || "One objective. Many immutable checkpoints. Ready for whoever continues it."}</p></div><div className="heading-actions">{project && <button className="button button-primary" type="button" onClick={() => { setNewWorkError(""); setWorkDialog(true); }}><Icon name="plus" size={16} />New work</button>}<button className="button button-secondary refresh-button" type="button" disabled={projectsLoading || listLoading} onClick={() => { setProjectsRefresh((value) => value + 1); setRefresh((value) => value + 1); }}><Icon name="refresh" size={16} /><span>Refresh</span></button></div></section>

        {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> :
          projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> :
          !projects.length ? <section className="empty-state onboarding"><div className="empty-art"><Icon name="library" size={34} /><span /></div><div className="eyebrow">A DURABLE PLACE TO CONTINUE</div><h2>Create your first project.</h2><p>Projects hold stable objectives and the session checkpoints that move them forward.</p><button className="button button-primary" onClick={() => setProjectDialog(true)}><Icon name="plus" size={17} />Create your first project</button></section> : <>
            <WorkItemList
              query={query}
              searchedQuery={search}
              searchRef={searchRef}
              semantic={semantic}
              status={status}
              results={results}
              loading={listLoading}
              error={listError}
              offset={offset}
              copiedKey={copied}
              onQuery={setQuery}
              onToggleSemantic={() => { setSemantic((value) => !value); setOffset(0); }}
              onStatus={(value) => { setStatus(value); setOffset(0); }}
              onRetry={() => setRefresh((value) => value + 1)}
              onClearFilters={() => { setQuery(""); setSearch(""); setStatus("open"); setOffset(0); }}
              onCreate={() => setWorkDialog(true)}
              onOpen={(item) => openWork(item)}
              onEdit={(item) => openWork(item, true)}
              onDelete={(item) => { setDeleteTarget(item.work_item); setDeleteError(""); }}
              onCopy={(value, key, success) => void copyText(value, key, success)}
              onOffset={setOffset}
            />
          </>}
      </div>
    </main>

    {notice && <div className={`toast ${notice.error ? "toast-error" : ""}`} role={notice.error ? "alert" : "status"}><Icon name={notice.error ? "close" : "check"} size={18} /><span>{notice.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><Icon name="close" size={16} /></button></div>}

    {projectDialog && <Dialog title="Create a project" onClose={() => { if (!projectSaving) setProjectDialog(false); }} busy={projectSaving}><form className="form-stack" onSubmit={(event) => void createProject(event)}>
      <label className="field">Project name<input name="name" required maxLength={120} autoFocus /></label>
      <label className="field">Project slug <span className="optional">Optional</span><input name="slug" maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" /></label>
      <label className="field">Description <span className="optional">Optional</span><textarea name="description" rows={3} maxLength={4000} /></label>
      <label className="field">Repository URL <span className="optional">Optional</span><input name="repository_url" type="url" maxLength={2000} /></label>
      {newProjectError && <ErrorNotice message={newProjectError} />}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={projectSaving} onClick={() => setProjectDialog(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={projectSaving}>{projectSaving ? "Creating…" : "Create project"}</button></div>
    </form></Dialog>}

    {workDialog && project && <Dialog title="Create durable work" onClose={() => { if (!workSaving) setWorkDialog(false); }} wide busy={workSaving}><form className="form-stack" onSubmit={(event) => void createWork(event)}>
      <p className="dialog-intro">The objective remains editable. Its initial checkpoint is immutable and attributed to this dashboard session.</p>
      <label className="field">Title<input name="title" required maxLength={200} autoFocus placeholder="What durable objective should survive this session?" /></label>
      <label className="field">Summary<textarea name="summary" required rows={3} maxLength={1000} placeholder="When is this work relevant?" /></label>
      <label className="field field-half">Priority<input name="priority" type="number" min={0} max={100} defaultValue={0} /></label>
      <label className="field">Initial context checkpoint<textarea className="prompt-editor" name="prompt" required rows={14} maxLength={100000} spellCheck={false} placeholder="Context, intended outcome, references, hazards, and verification…" /><span className="field-hint">Saved exactly as entered. Corrections become new checkpoints.</span></label>
      <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input name="repository_branch" maxLength={200} /></label><label className="field">Verified commit<input name="verified_against" className="mono" maxLength={64} /></label><label className="field">Tags <span className="optional">Comma separated</span><input name="tags" /></label></div></details>
      {newWorkError && <ErrorNotice message={newWorkError} />}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={workSaving} onClick={() => setWorkDialog(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={workSaving}>{workSaving ? "Creating…" : "Create work and checkpoint"}</button></div>
    </form></Dialog>}

    {opened && <Dialog title={mode === "edit" ? "Edit work item" : "Work context"} onClose={closeWork} wide busy={editSaving || checkpointSaving}>
      {contextLoading && !context ? <div className="loading-state" role="status"><span className="spinner" />Recalling work context…</div> :
        contextError && !context ? <ErrorNotice message={contextError}><button className="button button-secondary" onClick={() => void loadContext(opened)}>Try again</button></ErrorNotice> :
        context && <WorkItemDetail
          opened={opened}
          context={context}
          mode={mode}
          editDraft={editDraft}
          editSaving={editSaving}
          editError={editError}
          conflict={conflict}
          copiedKey={copied}
          checkpointPage={checkpointPage}
          checkpointOffset={checkpointOffset}
          checkpointLoading={checkpointLoading}
          checkpointLoadError={checkpointLoadError}
          checkpointActionError={checkpointActionError}
          checkpointKind={checkpointKind}
          checkpointBody={checkpointBody}
          checkpointBranch={checkpointBranch}
          checkpointCommit={checkpointCommit}
          checkpointTags={checkpointTags}
          checkpointSaving={checkpointSaving}
          setEditDraft={(updater) => setEditDraft((draft) => draft ? updater(draft) : draft)}
          onSaveEdits={(event) => void saveWorkEdits(event)}
          onCancelEdit={() => { setMode("view"); setEditDraft(draftFromWork(context.work_item)); setEditError(""); setConflict(null); }}
          onLoadCurrent={() => void loadLatestWork()}
          onUseCurrentVersion={useCurrentVersion}
          onEdit={() => { setEditDraft(draftFromWork(context.work_item)); setMode("edit"); }}
          onDelete={() => { setDeleteTarget(context.work_item); setDeleteError(""); }}
          onCopy={(value, key, success) => void copyText(value, key, success)}
          onCheckpointKind={setCheckpointKind}
          onCheckpointBody={setCheckpointBody}
          onCheckpointBranch={setCheckpointBranch}
          onCheckpointCommit={setCheckpointCommit}
          onCheckpointTags={setCheckpointTags}
          onAppend={() => void saveCheckpoint(false)}
          onComplete={() => void saveCheckpoint(true)}
          onCheckpointOffset={setCheckpointOffset}
          onReloadCheckpoints={() => setCheckpointRefresh((value) => value + 1)}
        />}
    </Dialog>}

    {deleteTarget && <Dialog title="Delete this work item?" onClose={() => { if (!deleting) setDeleteTarget(null); }} busy={deleting}>
      <p className="dialog-intro">This hides the objective and all checkpoints from ordinary reads. Immutable history remains recoverable in the database.</p>
      <div className="delete-preview"><StatusBadge status={deleteTarget.status} /><h3>{deleteTarget.title}</h3><p>{deleteTarget.summary}</p><span>Version {deleteTarget.version} · {formatDate(deleteTarget.updated_at)}</span></div>
      {deleteError && <ErrorNotice message={deleteError} />}
      <div className="dialog-actions"><button className="button button-secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>Keep work item</button><button className="button button-danger" disabled={deleting} onClick={() => void deleteWork()}>{deleting ? "Working…" : "Delete work item"}</button></div>
    </Dialog>}
  </div>;
}
