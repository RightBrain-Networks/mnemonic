"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { CHECKPOINT_PAGE_SIZE } from "@/components/checkpoint-timeline";
import ProjectSettingsPanel from "@/components/project-settings";
import WorkItemDetail from "@/components/work-item-detail";
import WorkItemList, { WORK_PAGE_SIZE } from "@/components/work-item-list";
import { StatusBadge, formatDate } from "@/components/work-item-card";
import { setDisplayTimeZone } from "@/lib/display-time";
import { draftFromWork, type WorkEditDraft } from "@/components/work-item-editor";
import { api, ApiError, errorMessage, isVersionConflict, workItemPath } from "@/lib/api";
import { currentContext } from "@/lib/current-context";
import { dashboardSessionId } from "@/lib/dashboard-session";
import {
  MutationIntentProvider,
  MutationIntentRegistry,
  mutationCreateKey,
  mutationWorkKey,
  useMutationIntents,
  useMutationUnloadWarning,
  type MutationIntentSummary
} from "@/lib/mutation-intent";
import {
  dashboardSortPreference,
  dashboardStatusPreference,
  dashboardStorageKeys
} from "@/lib/dashboard-preferences";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { connectLiveSync, invalidatesOpenWork, type LiveSyncStatus } from "@/lib/live-sync";
import {
  isBlockingProjectSettingsLoad,
  isCurrentProjectSettingsLoad
} from "@/lib/project-settings";
import type {
  Checkpoint,
  CheckpointInput,
  CheckpointKind,
  CompletionResult,
  DeletionResult,
  HierarchySummary,
  Page,
  Project,
  ProjectSettings,
  StatusFilter,
  WorkContext,
  WorkCreateInput,
  WorkCreation,
  WorkDeletionInput,
  WorkItem,
  WorkPatch,
  WorkSort,
  WorkSummary
} from "@/lib/types";
import { editableLifecycleStatuses, normalizedTags } from "@/lib/work-item-view";
import { dashboardMutationActor } from "@/lib/work-events";
import { workSearchParams } from "@/lib/work-item-search";
import { workRecallPointer } from "@/lib/work-recall-pointer";

const mutationLabels: Record<MutationIntentSummary["kind"], string> = {
  create_work: "Create work",
  add_checkpoint: "Add checkpoint",
  append_event: "Append progress",
  add_relationship: "Add relationship",
  update_work: "Update work",
  defer_work: "Defer work",
  complete_work: "Complete work",
  delete_work: "Delete work",
  remove_relationship: "Remove relationship"
};

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  plus: "M12 5v14M5 12h14",
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z",
  settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm7.4-.5 1.6 1-2 3.5-1.8-1a8 8 0 0 1-2.2 1.3V22h-4v-2.2a8 8 0 0 1-2.2-1.3l-1.8 1L5 16l1.6-1a8 8 0 0 1 0-2L5 12l2-3.5 1.8 1A8 8 0 0 1 11 8.2V6h4v2.2a8 8 0 0 1 2.2 1.3l1.8-1 2 3.5-1.6 1a8 8 0 0 1 0 2Z",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

function Logo() {
  return <svg className="logo-mark" width="34" height="34" viewBox="0 0 34 34" fill="none" aria-hidden="true"><rect width="34" height="34" rx="10" fill="currentColor" /><path d="M9 27v-4.2A10.6 10.6 0 0 1 6.5 15C6.5 9.5 10.9 5 16.4 5c4.7 0 8.7 3.4 9.5 8l1.9 3.1c.6 1-.05 2.25-1.2 2.33l-1.4.1-.4 3.3a3.4 3.4 0 0 1-3.4 3h-2.2V27H9Z" fill="#f9f8f3" /><rect x="14.3" y="9.2" width="4.1" height="9" rx="2.05" fill="currentColor" /><circle cx="16.35" cy="21.5" r="2.1" fill="currentColor" /></svg>;
}

function MutationRecoveryPanel({
  intents,
  retryingMutation,
  onRetry,
  modal = false
}: {
  intents: readonly MutationIntentSummary[];
  retryingMutation: string;
  onRetry: (intent: MutationIntentSummary) => void;
  modal?: boolean;
}) {
  if (!intents.length) return null;
  return <section
    className={`mutation-recovery ${modal ? "mutation-recovery-modal" : "mutation-recovery-global"}`}
    role="alert"
    aria-live="polite"
  >
    <div>
      <strong>Pending mutations need this tab.</strong>
      <span>Do not reload or close it; the exact retry request exists only in memory.</span>
    </div>
    <ul>{intents.map((intent) => <li key={intent.slot}>
      <span>{mutationLabels[intent.kind]} · {intent.state === "in_flight"
        ? "waiting for a response"
        : intent.state === "safety_conflict"
          ? "safety conflict"
          : "outcome unknown"}</span>
      {intent.state === "safety_conflict"
        && <small>Stop and inspect the client and server state before continuing.</small>}
      {intent.state === "unresolved" && <button
        type="button"
        className="button button-secondary"
        disabled={Boolean(retryingMutation)}
        onClick={() => onRetry(intent)}
      >{retryingMutation === intent.slot ? "Retrying…" : "Retry exact request"}</button>}
    </li>)}</ul>
  </section>;
}

function Dialog({
  title,
  children,
  onClose,
  recovery,
  wide = false,
  busy = false
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  recovery?: ReactNode;
  wide?: boolean;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, []);
  return <dialog ref={ref} className={`dialog ${wide ? "dialog-wide" : ""}`} aria-labelledby={titleId} onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="dialog-header"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose} disabled={busy}><Icon name="close" /></button></div>
    {recovery}
    <div className="dialog-content">{children}</div>
  </dialog>;
}

function ErrorNotice({ message, children }: { message: string; children?: ReactNode }) {
  return <div className="error-notice" role="alert"><p>{message}</p>{children}</div>;
}

function checkpointPayload(
  prompt: string,
  branch = "",
  commit = "",
  tagText = ""
): CheckpointInput {
  const verified = commit.trim().toLowerCase();
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
    current_context: currentContext(context),
    readiness: context.readiness
  };
}

type ContextLoadResult = "loaded" | "superseded" | "failed";

export default function Dashboard({ view = "library", timeZone }: { view?: "library" | "settings"; timeZone?: string | null; }) {
  setDisplayTimeZone(timeZone);
  const [mutationRegistry] = useState(() => new MutationIntentRegistry());
  const mutationIntents = useMutationIntents(mutationRegistry);
  useMutationUnloadWarning(mutationRegistry);
  const [retryingMutation, setRetryingMutation] = useState("");
  const dispatchedMutationIntents = mutationIntents.filter((intent) => intent.state !== "prepared");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState("");
  const [projectsRefresh, setProjectsRefresh] = useState(0);
  const [activeId, setActiveId] = useState("");
  const [projectDialog, setProjectDialog] = useState(false);
  const [projectSaving, setProjectSaving] = useState(false);
  const [newProjectError, setNewProjectError] = useState("");
  const [projectSettings, setProjectSettings] = useState<ProjectSettings | null>(null);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState("");
  const [settingsRefresh, setSettingsRefresh] = useState(0);

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [sort, setSort] = useState<WorkSort>("updated");
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [results, setResults] = useState<Page<WorkSummary | HierarchySummary> | null>(null);
  const [resultsViewKey, setResultsViewKey] = useState("");
  const [listLoading, setListLoading] = useState(false);
  const [listFailure, setListFailure] = useState<{
    viewKey: string;
    message: string;
  } | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const [workDialog, setWorkDialog] = useState(false);
  const [workSaving, setWorkSaving] = useState(false);
  const [newWorkError, setNewWorkError] = useState("");

  const [opened, setOpened] = useState<WorkSummary | null>(null);
  const [context, setContext] = useState<WorkContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [contextReconciliationRequired, setContextReconciliationRequired] = useState(false);
  const [contextRefresh, setContextRefresh] = useState(0);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editDraft, setEditDraft] = useState<WorkEditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const recordRequest = useRef(0);
  const lastLoadedContextRequest = useRef(0);

  const [checkpointPage, setCheckpointPage] = useState<Page<Checkpoint> | null>(null);
  const [checkpointOffset, setCheckpointOffset] = useState(0);
  const [checkpointLoading, setCheckpointLoading] = useState(false);
  const [checkpointLoadError, setCheckpointLoadError] = useState("");
  const [checkpointActionError, setCheckpointActionError] = useState("");
  const [checkpointRefresh, setCheckpointRefresh] = useState(0);
  const [eventRefresh, setEventRefresh] = useState(0);
  const [checkpointKind, setCheckpointKind] = useState<Exclude<CheckpointKind, "completion">>("progress");
  const [checkpointBody, setCheckpointBody] = useState("");
  const [checkpointBranch, setCheckpointBranch] = useState("");
  const [checkpointCommit, setCheckpointCommit] = useState("");
  const [checkpointTags, setCheckpointTags] = useState("");
  const [checkpointSaving, setCheckpointSaving] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<WorkItem | null>(null);
  const [deferringId, setDeferringId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; error?: boolean } | null>(null);
  const [liveSyncStatus, setLiveSyncStatus] = useState<LiveSyncStatus>("connecting");
  const project = projects.find((item) => item.id === activeId);
  const listViewKey = JSON.stringify([activeId, status, sort, offset, search, semantic]);
  const visibleResults = resultsViewKey === listViewKey ? results : null;
  const visibleListError = listFailure?.viewKey === listViewKey ? listFailure.message : "";
  const activeIdRef = useRef(activeId);
  const openedRef = useRef(opened);
  const settingsLoadController = useRef<AbortController | null>(null);
  const settingsLoadGeneration = useRef(0);
  const lastContextRefresh = useRef(0);
  const nextLeaseExpiry = earliestLeaseExpiry([
    ...(visibleResults?.items.map((item) => ("summary" in item ? item.summary : item).readiness.active_lease?.expires_at) ?? []),
    context?.readiness.active_lease?.expires_at
  ]);

  useEffect(() => { activeIdRef.current = activeId; }, [activeId]);
  useEffect(() => { openedRef.current = opened; }, [opened]);

  useEffect(() => {
    try {
      setStatus(dashboardStatusPreference(localStorage.getItem(dashboardStorageKeys.status)));
      setSort(dashboardSortPreference(localStorage.getItem(dashboardStorageKeys.sort)));
    } catch {
      // Preferences are optional when storage is unavailable.
    }
    setPreferencesReady(true);
  }, []);

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
      try { saved = localStorage.getItem(dashboardStorageKeys.project) ?? ""; } catch { /* optional */ }
      setActiveId((current) => all.some((item) => item.id === current) ? current : all.find((item) => item.id === saved)?.id ?? all[0]?.id ?? "");
    }
    load().catch((error) => { if (!controller.signal.aborted) setProjectsError(errorMessage(error)); })
      .finally(() => { if (!controller.signal.aborted) setProjectsLoading(false); });
    return () => controller.abort();
  }, [projectsRefresh]);

  useEffect(() => {
    if (!activeId) return;
    try { localStorage.setItem(dashboardStorageKeys.project, activeId); } catch { /* optional */ }
  }, [activeId]);

  useEffect(() => {
    if (!preferencesReady) return;
    try {
      localStorage.setItem(dashboardStorageKeys.status, status);
      localStorage.setItem(dashboardStorageKeys.sort, sort);
    } catch {
      // Preferences are optional when storage is unavailable.
    }
  }, [preferencesReady, sort, status]);

  useEffect(() => {
    const generation = ++settingsLoadGeneration.current;
    settingsLoadController.current?.abort();
    settingsLoadController.current = null;
    if (!activeId) {
      setProjectSettings(null);
      setSettingsLoading(false);
      setSettingsLoadError("");
      return;
    }
    const controller = new AbortController();
    settingsLoadController.current = controller;
    const blockingLoad = isBlockingProjectSettingsLoad(activeId, projectSettings);
    setProjectSettings((current) => current?.project_id === activeId ? current : null);
    setSettingsLoading(blockingLoad);
    setSettingsLoadError("");
    api<ProjectSettings>(`/projects/${encodeURIComponent(activeId)}/settings`, {
      signal: controller.signal
    })
      .then((loaded) => {
        if (!isCurrentProjectSettingsLoad(
          generation,
          settingsLoadGeneration.current,
          controller.signal.aborted
        )) return;
        if (loaded.project_id !== activeId) {
          throw new Error("Mnemonic returned settings for a different project.");
        }
        setProjectSettings(loaded);
      })
      .catch((error) => {
        if (isCurrentProjectSettingsLoad(
          generation,
          settingsLoadGeneration.current,
          controller.signal.aborted
        )) setSettingsLoadError(errorMessage(error));
      })
      .finally(() => {
        if (!isCurrentProjectSettingsLoad(
          generation,
          settingsLoadGeneration.current,
          controller.signal.aborted
        )) return;
        if (settingsLoadController.current === controller) {
          settingsLoadController.current = null;
        }
        setSettingsLoading(false);
      });
    return () => {
      controller.abort();
      if (settingsLoadController.current === controller) {
        settingsLoadController.current = null;
      }
    };
  }, [activeId, settingsRefresh]);

  useEffect(() => {
    const timer = setTimeout(() => { setSearch(query.trim()); setOffset(0); }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (view !== "library" || !activeId || !preferencesReady) {
      setResults(null);
      setResultsViewKey("");
      setListFailure(null);
      return;
    }
    const controller = new AbortController();
    const requestedViewKey = listViewKey;
    setListLoading(true);
    setListFailure(null);
    const params = workSearchParams({ status, sort, limit: WORK_PAGE_SIZE, offset, query: search, semantic });
    api<Page<WorkSummary | HierarchySummary>>(`${workItemPath(activeId)}?${params}`, { signal: controller.signal })
      .then((page) => {
        if (controller.signal.aborted) return;
        if (offset > 0 && offset >= page.total) {
          setOffset(Math.max(0, Math.floor((page.total - 1) / WORK_PAGE_SIZE) * WORK_PAGE_SIZE));
          return;
        }
        setResults(page);
        setResultsViewKey(requestedViewKey);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setListFailure({ viewKey: requestedViewKey, message: errorMessage(error) });
        }
      })
      .finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [activeId, listViewKey, offset, preferencesReady, refresh, search, semantic, sort, status, view]);

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
    const pending = { projects: false, settings: false, list: false, open: false };
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    function flush() {
      refreshTimer = undefined;
      if (pending.projects) setProjectsRefresh((value) => value + 1);
      if (pending.settings) setSettingsRefresh((value) => value + 1);
      if (pending.list) setRefresh((value) => value + 1);
      if (pending.open) {
        setCheckpointRefresh((value) => value + 1);
        setEventRefresh((value) => value + 1);
        setContextRefresh((value) => value + 1);
      }
      pending.projects = false;
      pending.settings = false;
      pending.list = false;
      pending.open = false;
    }

    function schedule() {
      if (refreshTimer === undefined) refreshTimer = setTimeout(flush, 75);
    }

    const disconnect = connectLiveSync((message) => {
      if (message.type === "ready") {
        pending.projects = true;
        pending.settings = true;
        pending.list = true;
        pending.open = true;
        schedule();
        return;
      }
      if (message.scope === "projects") {
        pending.projects = true;
        if (message.project_id === null || message.project_id === activeIdRef.current) {
          pending.settings = true;
        }
      } else {
        if (message.project_id === activeIdRef.current) pending.list = true;
        const openedWork = openedRef.current?.work_item;
        if (openedWork && invalidatesOpenWork(message, openedWork.project_id, openedWork.id)) {
          pending.open = true;
        }
      }
      schedule();
    }, setLiveSyncStatus);

    return () => {
      disconnect();
      if (refreshTimer !== undefined) clearTimeout(refreshTimer);
    };
  }, []);

  useEffect(() => {
    if (contextRefresh === lastContextRefresh.current) return;
    if (!opened) {
      lastContextRefresh.current = contextRefresh;
      return;
    }
    if (mode === "edit") return;
    lastContextRefresh.current = contextRefresh;
    void loadContext(opened, ++recordRequest.current);
  }, [contextRefresh, mode, opened]);

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
    if (!nextLeaseExpiry) return;
    return scheduleLeaseExpiryRefresh(nextLeaseExpiry, () => {
      setRefresh((value) => value + 1);
      if (opened) setContextRefresh((value) => value + 1);
    });
  }, [nextLeaseExpiry, opened?.work_item.id]);

  useEffect(() => {
    if (view !== "library") return;
    function focusSearch(event: KeyboardEvent) {
      const target = event.target as HTMLElement;
      if (event.key === "/" && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) && !target.isContentEditable && !document.querySelector("dialog[open]")) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, [view]);

  function chooseProject(id: string) {
    if (
      id !== activeId
      && activeId
      && mutationRegistry.hasDispatchedForProject(activeId)
    ) {
      setNotice({
        message: "Resolve pending mutations before switching projects. Reloading would lose the exact retry request.",
        error: true
      });
      return;
    }
    setActiveId(id);
    setOffset(0);
    setQuery("");
    setSearch("");
    setSemantic(false);
    setResults(null);
    setResultsViewKey("");
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
      const payload: WorkCreateInput = {
        title: String(form.get("title") ?? ""),
        summary: String(form.get("summary") ?? ""),
        priority: Number(form.get("priority") ?? 0),
        status: "pending",
        initial_checkpoint: initialCheckpoint
      };
      const created = await mutationRegistry.execute({
        kind: "create_work",
        slot: `create-work:${project.id}`,
        projectId: project.id,
        conflictKeys: [mutationCreateKey(project.id)],
        method: "POST",
        path: workItemPath(project.id),
        payload
      });
      setWorkDialog(false);
      setStatus("pending");
      setOffset(0);
      setRefresh((value) => value + 1);
      setNotice({ message: `“${created.work_item.title}” now has its first immutable checkpoint.` });
    } catch (error) {
      setNewWorkError(errorMessage(error));
    } finally {
      setWorkSaving(false);
    }
  }

  function handleProjectSettingsSaved(saved: ProjectSettings) {
    if (saved.project_id !== activeIdRef.current) return;
    settingsLoadGeneration.current += 1;
    settingsLoadController.current?.abort();
    settingsLoadController.current = null;
    setProjectSettings(saved);
    setSettingsLoading(false);
    setSettingsLoadError("");
    setSettingsRefresh((value) => value + 1);
  }

  async function loadContext(
    summary: WorkSummary,
    requestId = ++recordRequest.current,
    preserveEditDraft = false
  ): Promise<ContextLoadResult> {
    setContextLoading(true);
    setContextError("");
    try {
      const full = await api<WorkContext>(`${workItemPath(summary.work_item.project_id, summary.work_item.id)}/context?recent_limit=5&recent_event_limit=10`);
      if (recordRequest.current !== requestId) return "superseded";
      setContext(full);
      lastLoadedContextRequest.current = requestId;
      setContextReconciliationRequired(false);
      setOpened((current) => current ? summaryWithContext(current, full) : current);
      if (!preserveEditDraft) setEditDraft(draftFromWork(full.work_item));
      return "loaded";
    } catch (error) {
      if (recordRequest.current === requestId) setContextError(errorMessage(error));
      return recordRequest.current === requestId ? "failed" : "superseded";
    } finally {
      if (recordRequest.current === requestId) setContextLoading(false);
    }
  }

  function openWork(summary: WorkSummary, editing = false) {
    if (
      editing
      && mutationRegistry.blocks([
        mutationWorkKey(summary.work_item.project_id, summary.work_item.id)
      ])
    ) {
      setNotice({
        message: "Resolve the pending mutation for this work item before editing it.",
        error: true
      });
      return;
    }
    const requestId = ++recordRequest.current;
    setOpened(summary);
    setContext(null);
    setContextReconciliationRequired(false);
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
    setEventRefresh((value) => value + 1);
    void loadContext(summary, requestId);
  }

  function closeWork() {
    if (editSaving || checkpointSaving) return;
    if (
      opened
      && mutationRegistry.blocks([
        mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
      ])
    ) {
      setNotice({
        message: "Resolve the pending mutation before closing this work view.",
        error: true
      });
      return;
    }
    if (checkpointBody.trim() && !window.confirm("Discard your unsaved checkpoint?")) return;
    if (mode === "edit" && context && editDraft && JSON.stringify(editDraft) !== JSON.stringify(draftFromWork(context.work_item)) && !window.confirm("Discard your unsaved work-item edits?")) return;
    ++recordRequest.current;
    setOpened(null);
    setContext(null);
    setContextReconciliationRequired(false);
    setCheckpointPage(null);
    setCheckpointBody("");
  }

  async function reconcileContext(
    summary: WorkSummary,
    preserveEditDraft = false
  ): Promise<boolean> {
    const requestId = ++recordRequest.current;
    setContextReconciliationRequired(true);
    const result = await loadContext(summary, requestId, preserveEditDraft);
    if (
      result === "loaded"
      || result === "superseded" && lastLoadedContextRequest.current > requestId
    ) return true;
    if (result === "failed") setContext(null);
    return false;
  }

  async function reloadOpenContext(preserveEditDraft = false): Promise<boolean> {
    const current = openedRef.current;
    return current ? reconcileContext(current, preserveEditDraft) : false;
  }

  async function saveWorkEdits(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!context || !editDraft || !opened) return;
    const base = context.work_item;
    const patch: WorkPatch = { expected_version: base.version };
    if (editDraft.title !== base.title) patch.title = editDraft.title;
    if (editDraft.summary !== base.summary) patch.summary = editDraft.summary;
    if (editDraft.priority !== base.priority) patch.priority = editDraft.priority;
    if (!editableLifecycleStatuses(base.status).includes(editDraft.status)) {
      setEditError("That lifecycle transition is no longer available from the saved status.");
      return;
    }
    if (
      editDraft.status !== base.status
      && ["pending", "wont-do", "promoted"].includes(editDraft.status)
    ) {
      patch.status = editDraft.status as WorkPatch["status"];
    }
    if (Object.keys(patch).length === 1) { setMode("view"); return; }
    patch.actor = dashboardMutationActor(dashboardSessionId());
    setEditSaving(true);
    setEditError("");
    try {
      const saved = await mutationRegistry.execute({
        kind: "update_work",
        slot: `update-work:${base.project_id}:${base.id}`,
        projectId: base.project_id,
        conflictKeys: [mutationWorkKey(base.project_id, base.id)],
        method: "PATCH",
        path: workItemPath(base.project_id, base.id),
        payload: patch
      });
      const savedSummary = { ...opened, work_item: saved };
      setContext((value) => value ? { ...value, work_item: saved } : value);
      setOpened(savedSummary);
      setEventRefresh((value) => value + 1);
      setRefresh((value) => value + 1);
      const reconciled = await reconcileContext(savedSummary);
      if (!reconciled) {
        const message = "The work item was saved, but its current context could not be reloaded. Retry or use Refresh before continuing.";
        setContext(null);
        setMode("view");
        setEditError(message);
        setNotice({ message, error: true });
        return;
      }
      setConflict(null);
      setMode("view");
      setNotice({ message: "Work item saved. Checkpoint history was not changed." });
    } catch (error) {
      if (isVersionConflict(error)) {
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
      const latest = await api<WorkContext>(`${workItemPath(context.work_item.project_id, context.work_item.id)}/context?recent_limit=5&recent_event_limit=10`);
      setContext(latest);
      setOpened((value) => value ? summaryWithContext(value, latest) : value);
      setConflict(latest.work_item);
    } catch (error) {
      setEditError(errorMessage(error));
    }
  }

  function useCurrentVersion() {
    if (!conflict) return;
    setContext((value) => value ? { ...value, work_item: conflict } : value);
    setOpened((value) => value ? { ...value, work_item: conflict } : value);
    setEditDraft((value) => {
      if (!value || editableLifecycleStatuses(conflict.status).includes(value.status)) return value;
      return { ...value, status: conflict.status };
    });
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
        const result = await mutationRegistry.execute({
          kind: "complete_work",
          slot: `complete-work:${context.work_item.project_id}:${context.work_item.id}`,
          projectId: context.work_item.project_id,
          conflictKeys: [
            mutationWorkKey(context.work_item.project_id, context.work_item.id)
          ],
          method: "POST",
          path: `${base}/complete`,
          payload: { expected_version: context.work_item.version, checkpoint }
        });
        setNotice({ message: "Completion checkpoint recorded and work marked done." });
        setContext((value) => value ? {
          ...value,
          work_item: result.work_item,
          current_context: value.current_context,
          checkpoint_total: value.checkpoint_total + 1,
          readiness: {
            ...value.readiness,
            lifecycle_status: "done",
            is_terminal: true,
            has_active_lease: false,
            has_dropped_lease: false,
            active_lease: null,
            is_ready: false,
            display_state: "done"
          }
        } : value);
      } else {
        await mutationRegistry.execute({
          kind: "add_checkpoint",
          slot: `add-checkpoint:${context.work_item.project_id}:${context.work_item.id}`,
          projectId: context.work_item.project_id,
          conflictKeys: [
            mutationWorkKey(context.work_item.project_id, context.work_item.id)
          ],
          method: "POST",
          path: `${base}/checkpoints`,
          payload: { kind: checkpointKind, ...checkpoint }
        });
        setNotice({ message: checkpointKind === "context" ? "New current context recorded." : "Progress checkpoint recorded." });
      }
      setCheckpointBody("");
      setCheckpointBranch("");
      setCheckpointCommit("");
      setCheckpointTags("");
      setCheckpointOffset(0);
      setCheckpointRefresh((value) => value + 1);
      setEventRefresh((value) => value + 1);
      setRefresh((value) => value + 1);
      const reconciled = await reloadOpenContext();
      if (!reconciled) {
        const message = "The checkpoint was saved, but the current work context could not be reloaded. Retry or use Refresh before continuing.";
        setCheckpointActionError(message);
        setNotice({ message, error: true });
      }
    } catch (error) {
      if (complete && isVersionConflict(error)) {
        const reconciled = await reloadOpenContext();
        setCheckpointActionError(reconciled
          ? "This work item changed before completion. Your summary is still here; the current version has been reloaded for review."
          : "This work item changed before completion, and its current context could not be reloaded. Your summary is still here; retry or use Refresh before continuing.");
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
      const payload: WorkDeletionInput = {
        expected_version: deleteTarget.version,
        actor: dashboardMutationActor(dashboardSessionId())
      };
      await mutationRegistry.execute({
        kind: "delete_work",
        slot: `delete-work:${deleteTarget.project_id}:${deleteTarget.id}`,
        projectId: deleteTarget.project_id,
        conflictKeys: [mutationWorkKey(deleteTarget.project_id, deleteTarget.id)],
        method: "POST",
        path: `${workItemPath(deleteTarget.project_id, deleteTarget.id)}/delete`,
        payload
      });
      if (opened?.work_item.id === deleteTarget.id) {
        setOpened(null);
        setContext(null);
      }
      setDeleteTarget(null);
      setRefresh((value) => value + 1);
      setNotice({ message: "Work item removed from ordinary project views. Its history remains recoverable." });
    } catch (error) {
      if (isVersionConflict(error)) {
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

  async function toggleDeferral(summary: WorkSummary) {
    const work = summary.work_item;
    if (deferringId || (work.status !== "pending" && work.status !== "deferred")) return;
    const conflictKeys = [mutationWorkKey(work.project_id, work.id)];
    if (mutationRegistry.blocks(conflictKeys)) {
      setNotice({
        message: "Resolve the pending mutation for this work item before changing its queue state.",
        error: true
      });
      return;
    }
    setDeferringId(work.id);
    try {
      const actor = dashboardMutationActor(dashboardSessionId());
      if (work.status === "deferred") {
        await mutationRegistry.execute({
          kind: "update_work",
          slot: `update-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "PATCH",
          path: workItemPath(work.project_id, work.id),
          payload: {
            expected_version: work.version,
            status: "pending",
            actor
          }
        });
        setNotice({ message: `“${work.title}” is Pending and available in the work queue.` });
      } else {
        await mutationRegistry.execute({
          kind: "defer_work",
          slot: `defer-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "POST",
          path: `${workItemPath(work.project_id, work.id)}/defer`,
          payload: {
            expected_version: work.version,
            actor
          }
        });
        setNotice({ message: `“${work.title}” is Deferred and held out of the work queue.` });
      }
      setRefresh((value) => value + 1);
    } catch (error) {
      if (isVersionConflict(error)) setRefresh((value) => value + 1);
      setNotice({ message: errorMessage(error), error: true });
    } finally {
      setDeferringId(null);
    }
  }

  function openDeletion(item: WorkItem) {
    if (mutationRegistry.blocks([mutationWorkKey(item.project_id, item.id)])) {
      setNotice({
        message: "Resolve the pending mutation for this work item before deleting it.",
        error: true
      });
      return;
    }
    setDeleteTarget(item);
    setDeleteError("");
  }

  async function retryMutation(intent: MutationIntentSummary) {
    if (retryingMutation || intent.state !== "unresolved") return;
    setRetryingMutation(intent.slot);
    let contextReconciled = true;
    try {
      await mutationRegistry.retry(intent.slot);
      setRefresh((value) => value + 1);
      setCheckpointRefresh((value) => value + 1);
      setEventRefresh((value) => value + 1);
      setContextRefresh((value) => value + 1);
      if (intent.kind === "create_work") setWorkDialog(false);
      if (intent.kind === "delete_work") {
        setDeleteTarget(null);
        if (opened && intent.conflictKeys.includes(
          mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
        )) {
          setOpened(null);
          setContext(null);
        }
      } else if (
        opened
        && intent.conflictKeys.includes(
          mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
        )
      ) {
        contextReconciled = await reloadOpenContext();
      }
      if (intent.kind === "add_checkpoint" || intent.kind === "complete_work") {
        setCheckpointBody("");
        setCheckpointBranch("");
        setCheckpointCommit("");
        setCheckpointTags("");
      }
      if (intent.kind === "update_work") setMode("view");
      if (!contextReconciled) {
        setContext(null);
        setNotice({
          message: `${mutationLabels[intent.kind]} resolved from its original request, but current state could not be reloaded. Use Refresh before continuing.`,
          error: true
        });
      } else {
        setNotice({
          message: `${mutationLabels[intent.kind]} resolved from its original request. Current views are reconciling.`
        });
      }
    } catch (error) {
      const message = errorMessage(error);
      setRefresh((value) => value + 1);
      if (
        error instanceof ApiError
        && openedRef.current
        && intent.conflictKeys.includes(mutationWorkKey(
          openedRef.current.work_item.project_id,
          openedRef.current.work_item.id
        ))
      ) {
        const reconciled = await reloadOpenContext(true);
        setNotice({
          message: reconciled
            ? `${message} Current state was reloaded; your draft is still retained.`
            : `${message} Current state could not be reloaded. Use Refresh before continuing.`,
          error: true
        });
      } else {
        setNotice({ message, error: true });
      }
    } finally {
      setRetryingMutation("");
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

  function copyRecallPointer(summary: WorkSummary) {
    const projectId = summary.work_item.project_id;
    const pointerProject = projects.find((item) => item.id === projectId);
    if (!pointerProject) {
      setNotice({
        message: "Project details are unavailable. Refresh the workspace and try again.",
        error: true
      });
      return;
    }
    if (!projectSettings || projectSettings.project_id !== projectId) {
      setNotice({
        message: settingsLoadError
          ? `Project settings could not be loaded. ${settingsLoadError} Use Refresh and try again.`
          : settingsLoading
            ? "Project settings are still loading. Wait a moment and try again."
            : "Project settings are unavailable. Use Refresh and try again.",
        error: true
      });
      return;
    }
    void copyText(
      workRecallPointer(summary, {
        template: projectSettings.recall_pointer_template ?? undefined,
        project: pointerProject
      }),
      `${summary.work_item.id}:pointer`,
      "Recall pointer copied. Paste it into a session with Mnemonic connected."
    );
  }

  async function copyProjectId() {
    if (project) await copyText(project.id, "project", `Project ID copied: ${project.id}`);
  }

  const activeProjectMutationBlocked = Boolean(
    activeId && mutationRegistry.hasDispatchedForProject(activeId)
  );
  const openedWorkMutationBlocked = Boolean(
    opened && mutationRegistry.blocks([
      mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
    ])
  );
  const createWorkMutationBlocked = Boolean(
    project && mutationRegistry.blocks([mutationCreateKey(project.id)])
  );
  const createDialogMutationIntents = workDialog && project
    ? dispatchedMutationIntents.filter((intent) => (
      intent.kind === "create_work"
      && intent.conflictKeys.includes(mutationCreateKey(project.id))
    ))
    : [];
  const deleteDialogMutationIntents = deleteTarget
    ? dispatchedMutationIntents.filter((intent) => (
      intent.kind === "delete_work"
      && intent.conflictKeys.includes(
        mutationWorkKey(deleteTarget.project_id, deleteTarget.id)
      )
    ))
    : [];
  const deleteDialogMutationSlots = new Set(
    deleteDialogMutationIntents.map((intent) => intent.slot)
  );
  const openedDialogMutationIntents = opened
    ? dispatchedMutationIntents.filter((intent) => (
      !deleteDialogMutationSlots.has(intent.slot)
      && intent.conflictKeys.includes(
        mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
      )
    ))
    : [];
  const modalMutationSlots = new Set([
    ...createDialogMutationIntents,
    ...openedDialogMutationIntents,
    ...deleteDialogMutationIntents
  ].map((intent) => intent.slot));
  const globalMutationIntents = dispatchedMutationIntents.filter(
    (intent) => !modalMutationSlots.has(intent.slot)
  );
  const modalRecovery = (intents: readonly MutationIntentSummary[]) => (
    intents.length
      ? <MutationRecoveryPanel
        intents={intents}
        retryingMutation={retryingMutation}
        onRetry={(intent) => void retryMutation(intent)}
        modal
      />
      : undefined
  );

  return <MutationIntentProvider registry={mutationRegistry}><div className="app-shell">
    <a className="skip-link" href="#main-content">{view === "settings" ? "Skip to project settings" : "Skip to work items"}</a>
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Mnemonic home" aria-disabled={activeProjectMutationBlocked || undefined} onClick={(event) => {
        if (!activeProjectMutationBlocked) return;
        event.preventDefault();
        setNotice({ message: "Resolve pending mutations before leaving this dashboard document.", error: true });
      }}><Logo /><span>mnemonic<span className="brand-period">.</span></span></a>
      <div className="workspace-picker">
        <label className="section-label" htmlFor="project-select">YOUR WORKSPACE</label>
        <div className="select-wrap"><select id="project-select" value={activeId} disabled={projectsLoading || !projects.length || activeProjectMutationBlocked} onChange={(event) => chooseProject(event.target.value)}>
          {!projects.length && <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>}
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><span className="select-chevron" aria-hidden="true">⌄</span></div>
        <button className="new-project-button" type="button" disabled={projectsLoading || activeProjectMutationBlocked} onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={15} />New project</button>
        {project && <button className="copy-project-button" type="button" title={`Project ID: ${project.id}`} onClick={() => void copyProjectId()}><Icon name="copy" size={13} />Copy project ID for your agent</button>}
      </div>
      <nav aria-label="Workspace navigation">
        <a className={`nav-item ${view === "library" ? "active" : ""}`} href="/" aria-current={view === "library" ? "page" : undefined} onClick={(event) => {
          if (!activeProjectMutationBlocked) return;
          event.preventDefault();
          setNotice({ message: "Resolve pending mutations before leaving this dashboard document.", error: true });
        }}><Icon name="library" /><span>Work library</span><Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "settings" ? "active" : ""}`} href="/settings" aria-current={view === "settings" ? "page" : undefined} onClick={(event) => {
          if (!activeProjectMutationBlocked) return;
          event.preventDefault();
          setNotice({ message: "Resolve pending mutations before leaving this dashboard document.", error: true });
        }}><Icon name="settings" /><span>Project settings</span><Icon name="arrow" size={15} /></a>
      </nav>
      <div className="sidebar-note"><img className="note-art" src="/img/robot.svg" alt="" width={115} height={115} aria-hidden="true" /><h2>Keep your agents on the same page.</h2><p>Work units are reserved and nothing is forgotten.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /><span>Local workspace</span><span className="mvp-label">WORK GRAPH</span></div>
    </aside>

    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><span>{project?.name || "Getting started"}</span>{view === "settings" && <><span className="breadcrumb-slash">/</span><span>Project settings</span></>}</div><span className="topbar-note"><span className="small-mark">m.</span>Context worth keeping</span></header>
      <div className="page-content">
        {view === "settings" ? <>
          <section className="page-heading"><div><div className="eyebrow">PROJECT CONFIGURATION</div><h1>Project settings<span>.</span></h1><p>{project ? `Control how Mnemonic hands off work from “${project.name}”.` : "Choose a project, then configure how Mnemonic hands off its work."}</p></div><div className="heading-actions"><button className="button button-secondary" type="button" onClick={() => { setProjectsRefresh((value) => value + 1); setSettingsRefresh((value) => value + 1); }}>Refresh</button><div className={`sync-status sync-status-${liveSyncStatus}`} role="status" aria-live="polite"><span className="sync-status-dot" />{liveSyncStatus === "live" ? "Live updates" : liveSyncStatus === "retrying" ? "Reconnecting…" : "Connecting…"}</div></div></section>
          {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> :
            projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> :
            <ProjectSettingsPanel
              key={project?.id ?? "no-project"}
              project={project}
              settings={projectSettings}
              loading={settingsLoading}
              loadError={settingsLoadError}
              onRetry={() => setSettingsRefresh((value) => value + 1)}
              onSaved={handleProjectSettingsSaved}
              onNotice={(message, error) => setNotice({ message, error })}
            />}
        </> : <>
          <section className="page-heading"><div><div className="eyebrow">DURABLE WORK FOR TEMPORARY SESSIONS</div><h1>Work library<span>.</span></h1><p>{project?.description || "One objective. Many immutable checkpoints. Ready for whoever continues it."}</p></div><div className="heading-actions"><button className="button button-secondary" type="button" onClick={() => { setProjectsRefresh((value) => value + 1); setRefresh((value) => value + 1); setCheckpointRefresh((value) => value + 1); setEventRefresh((value) => value + 1); setContextRefresh((value) => value + 1); }}>Refresh</button>{project && <button className="button button-primary" type="button" disabled={createWorkMutationBlocked} onClick={() => { setNewWorkError(""); setWorkDialog(true); }}><Icon name="plus" size={16} />New work</button>}<div className={`sync-status sync-status-${liveSyncStatus}`} role="status" aria-live="polite"><span className="sync-status-dot" />{liveSyncStatus === "live" ? "Live updates" : liveSyncStatus === "retrying" ? "Reconnecting…" : "Connecting…"}</div></div></section>

          {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> :
            projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> :
            !projects.length ? <section className="empty-state onboarding"><div className="empty-art"><Icon name="library" size={34} /><span /></div><div className="eyebrow">A DURABLE PLACE TO CONTINUE</div><h2>Create your first project.</h2><p>Projects hold stable objectives and the session checkpoints that move them forward.</p><button className="button button-primary" onClick={() => setProjectDialog(true)}><Icon name="plus" size={17} />Create your first project</button></section> : <>
              <WorkItemList
                query={query}
                searchedQuery={search}
                searchRef={searchRef}
                semantic={semantic}
                status={status}
                sort={sort}
                results={visibleResults}
                loading={listLoading || (!visibleResults && !visibleListError)}
                error={visibleListError}
                offset={offset}
                refreshKey={refresh}
                viewKey={listViewKey}
                copiedKey={copied}
                deferringId={deferringId}
                onQuery={setQuery}
                onToggleSemantic={() => { setSemantic((value) => !value); setOffset(0); }}
                onStatus={(value) => { setStatus(value); setOffset(0); }}
                onSort={(value) => { setSort(value); setOffset(0); }}
                onRetry={() => setRefresh((value) => value + 1)}
                onClearFilters={() => { setQuery(""); setSearch(""); setStatus("pending"); setOffset(0); }}
                onCreate={() => setWorkDialog(true)}
                onOpen={(item) => openWork(item)}
                onEdit={(item) => openWork(item, true)}
                onDelete={(item) => openDeletion(item.work_item)}
                onDefer={(item) => void toggleDeferral(item)}
                onCopyPointer={(item) => void copyRecallPointer(item)}
                onOffset={setOffset}
              />
            </>}
        </>}
      </div>
    </main>

    <MutationRecoveryPanel
      intents={globalMutationIntents}
      retryingMutation={retryingMutation}
      onRetry={(intent) => void retryMutation(intent)}
    />

    {notice && <div className={`toast ${notice.error ? "toast-error" : ""}`} role={notice.error ? "alert" : "status"}><Icon name={notice.error ? "close" : "check"} size={18} /><span>{notice.message}</span><button className="icon-button" aria-label="Dismiss notification" onClick={() => setNotice(null)}><Icon name="close" size={16} /></button></div>}

    {projectDialog && <Dialog title="Create a project" onClose={() => { if (!projectSaving) setProjectDialog(false); }} busy={projectSaving}><form className="form-stack" onSubmit={(event) => void createProject(event)}>
      <label className="field">Project name<input name="name" required maxLength={120} autoFocus /></label>
      <label className="field">Project slug <span className="optional">Optional</span><input name="slug" maxLength={100} pattern="[a-z0-9]+(-[a-z0-9]+)*" /></label>
      <label className="field">Description <span className="optional">Optional</span><textarea name="description" rows={3} maxLength={4000} /></label>
      <label className="field">Repository URL <span className="optional">Optional</span><input name="repository_url" type="url" maxLength={2000} /></label>
      {newProjectError && <ErrorNotice message={newProjectError} />}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={projectSaving} onClick={() => setProjectDialog(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={projectSaving}>{projectSaving ? "Creating…" : "Create project"}</button></div>
    </form></Dialog>}

    {workDialog && project && <Dialog title="Create durable work" onClose={() => { if (!workSaving && !createWorkMutationBlocked) setWorkDialog(false); }} recovery={modalRecovery(createDialogMutationIntents)} wide busy={workSaving || createWorkMutationBlocked}><form className="form-stack" onSubmit={(event) => void createWork(event)}>
      <p className="dialog-intro">The objective remains editable. Its initial checkpoint is immutable and attributed to this dashboard session.</p>
      <label className="field">Title<input name="title" required disabled={createWorkMutationBlocked} maxLength={200} autoFocus placeholder="What durable objective should survive this session?" /></label>
      <label className="field">Summary<textarea name="summary" required disabled={createWorkMutationBlocked} rows={3} maxLength={1000} placeholder="When is this work relevant?" /></label>
      <label className="field field-half">Priority<input name="priority" type="number" disabled={createWorkMutationBlocked} min={0} max={100} defaultValue={0} /></label>
      <label className="field">Initial context checkpoint<textarea className="prompt-editor" name="prompt" required disabled={createWorkMutationBlocked} rows={14} maxLength={100000} spellCheck={false} placeholder="Context, intended outcome, references, hazards, and verification…" /><span className="field-hint">Saved exactly as entered. Corrections become new checkpoints.</span></label>
      <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input name="repository_branch" disabled={createWorkMutationBlocked} maxLength={200} /></label><label className="field">Verified commit<input name="verified_against" disabled={createWorkMutationBlocked} className="mono" maxLength={64} /></label><label className="field">Tags <span className="optional">Comma separated</span><input name="tags" disabled={createWorkMutationBlocked} /></label></div></details>
      {newWorkError && <ErrorNotice message={newWorkError} />}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={workSaving || createWorkMutationBlocked} onClick={() => setWorkDialog(false)}>Cancel</button><button type="submit" className="button button-primary" disabled={workSaving || createWorkMutationBlocked}>{workSaving ? "Creating…" : "Create work and checkpoint"}</button></div>
    </form></Dialog>}

    {opened && <Dialog title={mode === "edit" ? "Edit work item" : "Work context"} onClose={closeWork} recovery={modalRecovery(openedDialogMutationIntents)} wide busy={editSaving || checkpointSaving || openedWorkMutationBlocked}>
      {contextReconciliationRequired && contextLoading ? <div className="loading-state" role="status"><span className="spinner" />Reconciling saved work context…</div> :
        contextReconciliationRequired ? <ErrorNotice message={contextError || "The saved mutation could not be reconciled with current work context."}><button className="button button-secondary" onClick={() => void loadContext(opened)}>Try again</button></ErrorNotice> :
        contextLoading && !context ? <div className="loading-state" role="status"><span className="spinner" />Recalling work context…</div> :
        contextError && !context ? <ErrorNotice message={contextError}><button className="button button-secondary" onClick={() => void loadContext(opened)}>Try again</button></ErrorNotice> :
        context && <>
          {contextError && <ErrorNotice message={contextError}><button className="button button-secondary" onClick={() => void loadContext(opened)}>Try again</button></ErrorNotice>}
          <WorkItemDetail
          opened={opened}
          context={context}
          mode={mode}
          editDraft={editDraft}
          editSaving={editSaving}
          mutationBlocked={openedWorkMutationBlocked}
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
          eventRefreshSignal={eventRefresh}
          setEditDraft={(updater) => setEditDraft((draft) => draft ? updater(draft) : draft)}
          onSaveEdits={(event) => void saveWorkEdits(event)}
          onCancelEdit={() => {
            if (openedWorkMutationBlocked) return;
            setMode("view");
            setEditDraft(draftFromWork(context.work_item));
            setEditError("");
            setConflict(null);
          }}
          onLoadCurrent={() => void loadLatestWork()}
          onUseCurrentVersion={useCurrentVersion}
          onEdit={() => { setEditDraft(draftFromWork(context.work_item)); setEditError(""); setConflict(null); setMode("edit"); }}
          onDelete={() => openDeletion(context.work_item)}
          onCopy={(value, key, success) => void copyText(value, key, success)}
          onCopyPointer={(item) => void copyRecallPointer(item)}
          onCheckpointKind={setCheckpointKind}
          onCheckpointBody={setCheckpointBody}
          onCheckpointBranch={setCheckpointBranch}
          onCheckpointCommit={setCheckpointCommit}
          onCheckpointTags={setCheckpointTags}
          onAppend={() => void saveCheckpoint(false)}
          onComplete={() => void saveCheckpoint(true)}
          onRelationshipsChanged={async () => {
            const reconciled = await reloadOpenContext();
            setEventRefresh((value) => value + 1);
            setRefresh((value) => value + 1);
            return reconciled;
          }}
          onCheckpointOffset={setCheckpointOffset}
          onReloadCheckpoints={() => setCheckpointRefresh((value) => value + 1)}
          onEventAppended={async () => {
            setRefresh((value) => value + 1);
            const reconciled = await reloadOpenContext();
            if (!reconciled) {
              setNotice({
                message: "Progress was saved, but current work context could not be reloaded. Use Refresh before continuing.",
                error: true
              });
            }
            return reconciled;
          }}
        /></>}
    </Dialog>}

    {deleteTarget && <Dialog title="Delete this work item?" onClose={() => { if (!deleting && !mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])) setDeleteTarget(null); }} recovery={modalRecovery(deleteDialogMutationIntents)} busy={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])}>
      <p className="dialog-intro">This hides the objective and all checkpoints from ordinary reads. Immutable history remains recoverable in the database.</p>
      <div className="delete-preview"><StatusBadge status={deleteTarget.status} /><h3>{deleteTarget.title}</h3><p>{deleteTarget.summary}</p><span>Version {deleteTarget.version} · {formatDate(deleteTarget.updated_at)}</span></div>
      {deleteError && <ErrorNotice message={deleteError} />}
      <div className="dialog-actions"><button className="button button-secondary" disabled={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])} onClick={() => setDeleteTarget(null)}>Keep work item</button><button className="button button-danger" disabled={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])} onClick={() => void deleteWork()}>{deleting ? "Working…" : "Delete work item"}</button></div>
    </Dialog>}
  </div></MutationIntentProvider>;
}
