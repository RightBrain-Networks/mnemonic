"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
  type ReactNode
} from "react";
import { CHECKPOINT_PAGE_SIZE } from "@/components/checkpoint-timeline";
import AffectedPathsEditor from "@/components/affected-paths-editor";
import DashboardViewChrome from "@/components/dashboard-view-chrome";
import ThemeSelector from "@/components/theme-selector";
import ProjectSettingsPanel from "@/components/project-settings";
import DuplicateSuggestionPanel from "@/components/duplicate-suggestion-panel";
import HumanAttentionList from "@/components/human-attention-list";
import MutationRecoveryPanel from "@/components/mutation-recovery-panel";
import WorkDetailPane from "@/components/work-detail-pane";
import WorkItemList from "@/components/work-item-list";
import { usePaneCrossfade } from "@/components/use-pane-crossfade";
import { useWorkQueuePages } from "@/components/use-work-queue-pages";
import { StatusBadge, formatDate } from "@/components/work-item-card";
import { setDisplayTimeZone } from "@/lib/display-time";
import { draftFromWork, type WorkEditDraft } from "@/components/work-item-editor";
import { api, ApiError, errorMessage, isVersionConflict, workItemPath } from "@/lib/api";
import { currentContext } from "@/lib/current-context";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { decodeWorkContext, decodeWorkItemDetail } from "@/lib/duplicate-handling";
import { decodeHumanAttentionPage, humanAttentionSearchParams } from "@/lib/human-gates";
import {
  MutationIntentProvider,
  MutationIntentRegistry,
  mutationCreateKey,
  mutationWorkKey,
  selectMutationScope,
  useMutationIntents,
  useMutationUnloadWarning,
  type MutationIntentSummary
} from "@/lib/mutation-intent";
import { mutationLabels, selectMutationRecovery } from "@/lib/mutation-recovery";
import {
  dashboardSortPreference,
  dashboardStatusPreference,
  dashboardStorageKeys
} from "@/lib/dashboard-preferences";
import { dialogOpen, typingTarget } from "@/lib/keyboard-shortcuts";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { connectLiveSync, type LiveSyncStatus } from "@/lib/live-sync";
import { projectShortcutIndex } from "@/lib/project-shortcuts";
import {
  isBlockingProjectSettingsLoad,
  isCurrentProjectSettingsLoad
} from "@/lib/project-settings";
import type {
  Checkpoint,
  CheckpointInput,
  CheckpointKind,
  DeletionResult,
  DuplicateScope,
  Page,
  Project,
  ProjectSettings,
  StatusFilter,
  WorkContext,
  WorkCreateInput,
  WorkCreation,
  WorkDeletionInput,
  WorkItem,
  WorkMergeResult,
  WorkPatch,
  WorkSort,
  WorkSummary
} from "@/lib/types";
import { validUuid } from "@/lib/wire-guards";
import type { DetailTab } from "@/lib/work-detail-tabs";
import { editableLifecycleStatuses, normalizedTags } from "@/lib/work-item-view";
import { dashboardMutationActor } from "@/lib/work-events";
import { paneCrossfadeTargets } from "@/lib/pane-crossfade";
import { scheduleHierarchyFilterCommit } from "@/lib/work-item-search";
import { statusFilterTransition } from "@/lib/work-queue";
import { workRecallPointer } from "@/lib/work-recall-pointer";
import {
  AffectedPathsValidationError,
  parseAffectedPathsDraft
} from "@/lib/affected-paths";
import { decodeCheckpointPage } from "@/lib/checkpoint-codecs";
import {
  completionEvidenceDraftIsEmpty,
  completionEvidenceDraftIssues,
  completionEvidenceFromDraft,
  emptyCompletionEvidenceDraft,
  type CompletionEvidenceDraft,
  type CompletionEvidenceIssue
} from "@/lib/completion-evidence";

const iconPaths = {
  search: "m21 21-4.4-4.4M19 10.5a8.5 8.5 0 1 1-17 0 8.5 8.5 0 0 1 17 0Z",
  plus: "M12 5v14M5 12h14",
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z",
  settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm7.4-.5 1.6 1-2 3.5-1.8-1a8 8 0 0 1-2.2 1.3V22h-4v-2.2a8 8 0 0 1-2.2-1.3l-1.8 1L5 16l1.6-1a8 8 0 0 1 0-2L5 12l2-3.5 1.8 1A8 8 0 0 1 11 8.2V6h4v2.2a8 8 0 0 1 2.2 1.3l1.8-1 2 3.5-1.6 1a8 8 0 0 1 0 2Z",
  attention: "M12 3a7 7 0 0 0-7 7v4l-2 3h18l-2-3v-4a7 7 0 0 0-7-7Zm-2 18h4",
  arrow: "M5 12h14m-5-5 5 5-5 5",
  back: "M19 12H5m5-5-5 5 5 5",
  box: "M4 8h16v13H4V8ZM2 3h20v5H2V3Zm7 10h6"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

// The brand mark carries fixed brand colors rather than currentColor, so it
// stays identical in both themes; `images/mnemonic_logo.svg` is the source.
function Logo() {
  return <svg className="logo-mark" width="40" height="38" viewBox="0 0 916 863.9" shapeRendering="geometricPrecision" aria-hidden="true">
    <path d="M458,99.9L458,99.9c13.3,0,24,10.7,24,24v48c0,13.3-10.7,24-24,24l0,0c-13.3,0-24-10.7-24-24v-48C434,110.7,444.7,99.9,458,99.9z" fill="#f25522" />
    <circle cx="467.3" cy="85.3" r="85.3" fill="#f25522" />
    <circle cx="473.7" cy="91.6" r="52.8" fill="#94db23" />
    <path d="M83,365.9L83,365.9c45.8,0,83,37.2,83,83v102c0,45.8-37.2,83-83,83l0,0c-45.8,0-83-37.2-83-83v-102C0,403.1,37.2,365.9,83,365.9z" fill="#f25522" />
    <path d="M833,365.9L833,365.9c45.8,0,83,37.2,83,83v102c0,45.8-37.2,83-83,83l0,0c-45.8,0-83-37.2-83-83v-102C750,403.1,787.2,365.9,833,365.9z" fill="#f25522" />
    <path d="M82,409.9L82,409.9c21,0,38,17,38,38v104c0,21-17,38-38,38l0,0c-21,0-38-17-38-38v-104C44,426.9,61,409.9,82,409.9z" fill="#94db23" />
    <path d="M834,409.9L834,409.9c21,0,38,17,38,38v104c0,21-17,38-38,38l0,0c-21,0-38-17-38-38v-104C796,426.9,813,409.9,834,409.9z" fill="#94db23" />
    <path d="M290,179.9h336c120.4,0,218,97.6,218,218v248c0,120.4-97.6,218-218,218H290c-120.4,0-218-97.6-218-218v-248C72,277.5,169.6,179.9,290,179.9z" fill="#f25522" />
    <path d="M298,241.9h320c89.5,0,162,72.5,162,162v234c0,89.5-72.5,162-162,162H298c-89.5,0-162-72.5-162-162v-234C136,314.4,208.5,241.9,298,241.9z" fill="#94db23" />
    <path d="M335.5,406.8C351.2,335.3,399.2,304,456.7,304c75,0,123.8,43.6,123.8,108.1c0,49.7-26.2,81.1-71.5,120.3c-34.9,30.5-53.2,56.7-53.2,87.2" fill="none" stroke="#ffffff" strokeWidth="88" strokeLinecap="round" strokeLinejoin="round" />
    <circle cx="460" cy="741.9" r="42" fill="#ffffff" />
  </svg>;
}

function Dialog({
  title,
  children,
  onClose,
  recovery,
  wide = false,
  busy = false,
  suspended = false
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  recovery?: ReactNode;
  wide?: boolean;
  busy?: boolean;
  // A suspended dialog is closed but keeps its children mounted, so an unsaved
  // draft survives while the user inspects something behind it.
  suspended?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (suspended) {
      if (dialog.open) dialog.close();
      return;
    }
    if (!dialog.open) dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, [suspended]);
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
  tagText = "",
  affectedPathsText = ""
): CheckpointInput {
  const affectedPaths = parseAffectedPathsDraft(affectedPathsText);
  const verified = commit.trim().toLowerCase();
  if (verified && !/^[a-fA-F0-9]{7,64}$/.test(verified)) {
    throw new Error("Verified commit must be a Git commit ID with 7–64 hexadecimal characters.");
  }
  if (affectedPaths.length > 0 && !verified) {
    throw new AffectedPathsValidationError({
      message: "Declared affected paths require a caller-asserted baseline commit."
    });
  }
  return {
    prompt,
    source_client: "dashboard",
    source_session_id: dashboardSessionId(),
    source_model: null,
    repository_branch: branch.trim() || null,
    verified_against: verified || null,
    ...(affectedPaths.length > 0 ? { affected_paths: affectedPaths } : {}),
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

function locationWorkSelection(): string | null {
  const value = new URLSearchParams(window.location.search).get("work");
  return value && validUuid(value) ? value : null;
}

type ContextLoadResult = "loaded" | "superseded" | "failed";
type WorkDialogState = "closed" | "open" | "suspended";

export default function Dashboard({ view = "library", timeZone }: { view?: "library" | "attention" | "settings"; timeZone?: string | null; }) {
  setDisplayTimeZone(timeZone);
  const [mutationRegistry] = useState(() => new MutationIntentRegistry());
  const mutationIntents = useMutationIntents(mutationRegistry);
  useMutationUnloadWarning(mutationRegistry);
  const [retryingMutation, setRetryingMutation] = useState("");
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
  const [duplicateScope, setDuplicateScope] = useState<DuplicateScope>("canonical");
  const [canonicalWorkItemId, setCanonicalWorkItemId] = useState("");
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [sort, setSort] = useState<WorkSort>("updated");
  const [tagInput, setTagInput] = useState("");
  const [sourceClientInput, setSourceClientInput] = useState("");
  const [sourceSessionInput, setSourceSessionInput] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [sourceClientFilter, setSourceClientFilter] = useState("");
  const [sourceSessionFilter, setSourceSessionFilter] = useState("");
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [attentionRefresh, setAttentionRefresh] = useState(0);
  const [attentionCount, setAttentionCount] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const [workDialog, setWorkDialog] = useState<WorkDialogState>("closed");
  const [workSaving, setWorkSaving] = useState(false);
  const [newWorkError, setNewWorkError] = useState("");
  const [createAffectedPathsError, setCreateAffectedPathsError] = useState("");
  const [suggestionDraftGeneration, setSuggestionDraftGeneration] = useState(0);

  const [opened, setOpened] = useState<WorkSummary | null>(null);
  const [context, setContext] = useState<WorkContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState("");
  const [contextReconciliationRequired, setContextReconciliationRequired] = useState(false);
  const [contextRefresh, setContextRefresh] = useState(0);
  const [tab, setTab] = useState<DetailTab>("context");
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editDraft, setEditDraft] = useState<WorkEditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const recordRequest = useRef(0);
  const lastLoadedContextRequest = useRef(0);
  const exactContextTarget = useRef<{ projectId: string; workItemId: string } | null>(null);
  // undefined until the address has been read once; then the pending `?work=` restore or null.
  const urlWorkRestore = useRef<string | null | undefined>(undefined);

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
  const [checkpointAffectedPaths, setCheckpointAffectedPaths] = useState("");
  const [checkpointAffectedPathsError, setCheckpointAffectedPathsError] = useState("");
  const [checkpointTags, setCheckpointTags] = useState("");
  const [checkpointSaving, setCheckpointSaving] = useState(false);
  const [completionEvidenceDraft, setCompletionEvidenceDraft] =
    useState<CompletionEvidenceDraft>(emptyCompletionEvidenceDraft);
  const [completionEvidenceIssues, setCompletionEvidenceIssues] =
    useState<readonly CompletionEvidenceIssue[]>([]);

  const [deleteTarget, setDeleteTarget] = useState<WorkItem | null>(null);
  const [deferringId, setDeferringId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; error?: boolean } | null>(null);
  const [liveSyncStatus, setLiveSyncStatus] = useState<LiveSyncStatus>("connecting");
  const project = projects.find((item) => item.id === activeId);
  const queue = useWorkQueuePages({
    enabled: view === "library" && Boolean(activeId) && preferencesReady,
    projectId: activeId,
    status,
    sort,
    search,
    semantic,
    duplicateScope,
    canonicalWorkItemId,
    tag: tagFilter,
    sourceClient: sourceClientFilter,
    sourceSessionId: sourceSessionFilter,
    refresh
  });
  const crossfade = usePaneCrossfade();
  const activeIdRef = useRef(activeId);
  const openedRef = useRef(opened);
  const settingsLoadController = useRef<AbortController | null>(null);
  const settingsLoadGeneration = useRef(0);
  const lastContextRefresh = useRef(0);
  const openedId = opened?.work_item.id ?? null;
  const nextLeaseExpiry = earliestLeaseExpiry([
    ...queue.items.flatMap((item) => [
      item.summary.readiness.active_lease?.expires_at,
      "presentation" in item
        ? item.presentation.next_active_descendant_lease_expires_at
        : null
    ]),
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
    if (!activeId) {
      setAttentionCount(null);
      return;
    }
    const controller = new AbortController();
    const params = humanAttentionSearchParams({ limit: 0 });
    api<unknown>(`/projects/${encodeURIComponent(activeId)}/human-attention?${params}`, {
      signal: controller.signal
    }).then((value) => {
      if (!controller.signal.aborted) {
        setAttentionCount(decodeHumanAttentionPage(value, activeId, { limit: 0 }).total);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setAttentionCount(null);
    });
    return () => controller.abort();
  }, [activeId, attentionRefresh]);

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
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    return scheduleHierarchyFilterCommit({
      tag: tagInput,
      sourceClient: sourceClientInput,
      sourceSessionId: sourceSessionInput
    }, {
      tag: tagFilter,
      sourceClient: sourceClientFilter,
      sourceSessionId: sourceSessionFilter
    }, (next) => {
      setTagFilter(next.tag);
      setSourceClientFilter(next.sourceClient);
      setSourceSessionFilter(next.sourceSessionId);
    });
  }, [sourceClientInput, sourceSessionInput, tagInput]);

  useEffect(() => {
    if (!opened) { setCheckpointPage(null); return; }
    const controller = new AbortController();
    setCheckpointLoading(true);
    setCheckpointLoadError("");
    const base = workItemPath(opened.work_item.project_id, opened.work_item.id);
    api<unknown>(`${base}/checkpoints?order=newest&limit=${CHECKPOINT_PAGE_SIZE}&offset=${checkpointOffset}`, { signal: controller.signal })
      .then((value) => {
        if (controller.signal.aborted) return;
        const page = decodeCheckpointPage(value, opened.work_item.id, {
          limit: CHECKPOINT_PAGE_SIZE,
          offset: checkpointOffset
        });
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
    const pending = { projects: false, settings: false, list: false, attention: false, open: false };
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    function flush() {
      refreshTimer = undefined;
      if (pending.projects) setProjectsRefresh((value) => value + 1);
      if (pending.settings) setSettingsRefresh((value) => value + 1);
      if (pending.list) setRefresh((value) => value + 1);
      if (pending.attention) setAttentionRefresh((value) => value + 1);
      if (pending.open) {
        setCheckpointRefresh((value) => value + 1);
        setEventRefresh((value) => value + 1);
        setContextRefresh((value) => value + 1);
      }
      pending.projects = false;
      pending.settings = false;
      pending.list = false;
      pending.attention = false;
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
        pending.attention = true;
        pending.open = true;
        schedule();
        return;
      }
      if (message.scope === "projects") {
        pending.projects = true;
        pending.settings = true;
      } else {
        pending.list = true;
        pending.attention = true;
        if (openedRef.current) pending.open = true;
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
    // A live-sync refresh must not supersede a context load already in flight (an exact audit
    // load, a ?work= restore, or a post-mutation reconcile); it runs once that load settles.
    if (contextLoading) return;
    lastContextRefresh.current = contextRefresh;
    void loadContext(opened, ++recordRequest.current);
  }, [contextLoading, contextRefresh, mode, opened]);

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
      setAttentionRefresh((value) => value + 1);
      if (opened) setContextRefresh((value) => value + 1);
    });
  }, [nextLeaseExpiry, opened?.work_item.id]);

  // The digits pick the workspace's first ten projects. The picker sits in the sidebar
  // of every view, so this is not scoped to the work library, and the switch routes
  // through chooseProject: a pending mutation or an unsaved draft still refuses it
  // there, exactly as it refuses a switch made with the pointer.
  // The listener re-registers each render deliberately. chooseProject reads the open
  // work item and its drafts from the current render, so a handler captured once would
  // decide against a stale pane.
  useEffect(() => {
    function selectProject(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (typingTarget(event.target) || dialogOpen()) return;
      const index = projectShortcutIndex(event.key);
      if (index === null) return;
      // The list a refresh is about to replace is still the list on screen, so the key
      // is answered from it rather than dropped for the duration of a background load.
      const target = projects[index];
      if (!target || target.id === activeId) return;
      event.preventDefault();
      chooseProject(target.id);
    }
    window.addEventListener("keydown", selectProject);
    return () => window.removeEventListener("keydown", selectProject);
  });

  useEffect(() => {
    if (view !== "library") return;
    function focusSearch(event: KeyboardEvent) {
      if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (typingTarget(event.target) || dialogOpen()) return;
      event.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, [view]);

  // The address is read once so a reload restores the selection; it is captured
  // before the mirror below could rewrite it.
  useEffect(() => {
    if (view !== "library" || urlWorkRestore.current !== undefined) return;
    urlWorkRestore.current = locationWorkSelection();
  }, [view]);

  useEffect(() => {
    if (view !== "library" || !activeId || opened) return;
    const target = urlWorkRestore.current;
    if (!target) return;
    urlWorkRestore.current = null;
    void openExactWork(activeId, target);
  }, [activeId, opened, view]);

  useEffect(() => {
    if (view !== "library" || urlWorkRestore.current !== null) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("work") === openedId) return;
    if (openedId) url.searchParams.set("work", openedId);
    else url.searchParams.delete("work");
    window.history.replaceState(window.history.state, "", url);
  }, [openedId, view]);

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
    if (id !== activeId && opened) {
      if (!leavingOpenedWorkAllowed()) return;
      clearSelection();
    }
    setActiveId(id);
    setQuery("");
    setSearch("");
    setSemantic(false);
    setDuplicateScope("canonical");
    setCanonicalWorkItemId("");
    setMergeOpen(false);
    setTagInput("");
    setSourceClientInput("");
    setSourceSessionInput("");
    setTagFilter("");
    setSourceClientFilter("");
    setSourceSessionFilter("");
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

  function openWorkDialog(): void {
    if (workDialog === "suspended") {
      setWorkDialog("open");
      return;
    }
    setNewWorkError("");
    setCreateAffectedPathsError("");
    setWorkDialog("open");
  }

  async function createWork(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!project) return;
    const form = new FormData(event.currentTarget);
    setWorkSaving(true);
    setNewWorkError("");
    setCreateAffectedPathsError("");
    try {
      const prompt = String(form.get("prompt") ?? "");
      const initialCheckpoint = checkpointPayload(
        prompt,
        String(form.get("repository_branch") ?? ""),
        String(form.get("verified_against") ?? ""),
        String(form.get("tags") ?? ""),
        String(form.get("affected_paths") ?? "")
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
      setWorkDialog("closed");
      setStatus("pending");
      setRefresh((value) => value + 1);
      setNotice({ message: `“${created.work_item.title}” now has its first immutable checkpoint.` });
      void openExactWork(project.id, created.work_item.id);
    } catch (error) {
      if (error instanceof AffectedPathsValidationError) {
        setCreateAffectedPathsError(error.message);
      } else {
        setNewWorkError(errorMessage(error));
      }
    } finally {
      setWorkSaving(false);
    }
  }

  async function inspectExistingWork(projectId: string, workItemId: string): Promise<void> {
    setWorkDialog("suspended");
    const loaded = await openExactWork(projectId, workItemId);
    // With nothing selected the pane cannot show the failure, so the draft comes back.
    if (!loaded && !openedRef.current) {
      setWorkDialog((value) => value === "suspended" ? "open" : value);
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
      const value = await api<unknown>(`${workItemPath(summary.work_item.project_id, summary.work_item.id)}/context?recent_limit=5&recent_event_limit=10`);
      const full = decodeWorkContext(
        value,
        summary.work_item.project_id,
        summary.work_item.id
      );
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

  function openWork(summary: WorkSummary) {
    const requestId = ++recordRequest.current;
    exactContextTarget.current = null;
    setOpened(summary);
    setContext(null);
    setContextReconciliationRequired(false);
    setContextError("");
    setMode("view");
    setMergeOpen(false);
    setEditDraft(draftFromWork(summary.work_item));
    setEditError("");
    setConflict(null);
    setCheckpointOffset(0);
    setCheckpointPage(null);
    setCheckpointBody("");
    setCheckpointBranch("");
    setCheckpointCommit("");
    setCheckpointAffectedPaths("");
    setCheckpointAffectedPathsError("");
    setCheckpointTags("");
    setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
    setCompletionEvidenceIssues([]);
    setCheckpointKind("progress");
    setCheckpointActionError("");
    setEventRefresh((value) => value + 1);
    void loadContext(summary, requestId);
  }

  async function openExactWork(projectId: string, workItemId: string): Promise<boolean> {
    const requestId = ++recordRequest.current;
    exactContextTarget.current = { projectId, workItemId };
    setContext(null);
    setContextReconciliationRequired(true);
    setContextLoading(true);
    setContextError("");
    setMode("view");
    setMergeOpen(false);
    setEditError("");
    setConflict(null);
    setCheckpointOffset(0);
    setCheckpointPage(null);
    setCheckpointBody("");
    setCheckpointBranch("");
    setCheckpointCommit("");
    setCheckpointAffectedPaths("");
    setCheckpointAffectedPathsError("");
    setCheckpointTags("");
    setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
    setCompletionEvidenceIssues([]);
    setCheckpointActionError("");
    try {
      const value = await api<unknown>(
        `${workItemPath(projectId, workItemId)}/context?recent_limit=5&recent_event_limit=10`
      );
      if (recordRequest.current !== requestId) return false;
      const full = decodeWorkContext(value, projectId, workItemId);
      const summary: WorkSummary = {
        work_item: full.work_item,
        checkpoint_count: full.checkpoint_total,
        ancestor_path: [],
        ancestor_path_truncated: false,
        current_context: currentContext(full),
        readiness: full.readiness
      };
      setOpened(summary);
      setContext(full);
      setEditDraft(draftFromWork(full.work_item));
      setContextReconciliationRequired(false);
      exactContextTarget.current = null;
      lastLoadedContextRequest.current = requestId;
      setEventRefresh((value) => value + 1);
      return true;
    } catch (cause) {
      if (recordRequest.current === requestId) {
        const message = errorMessage(cause);
        setContextError(message);
        // The pane only renders a failure beneath a selection; without one the toast carries it.
        if (!openedRef.current) setNotice({ message, error: true });
      }
      return false;
    } finally {
      if (recordRequest.current === requestId) setContextLoading(false);
    }
  }

  function clearSelection(): void {
    ++recordRequest.current;
    exactContextTarget.current = null;
    setOpened(null);
    setMergeOpen(false);
    setContext(null);
    setContextLoading(false);
    setContextReconciliationRequired(false);
    setCheckpointPage(null);
    setCheckpointBody("");
    setCheckpointBranch("");
    setCheckpointCommit("");
    setCheckpointAffectedPaths("");
    setCheckpointAffectedPathsError("");
    setCheckpointTags("");
    setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
    setCompletionEvidenceIssues([]);
  }

  function viewDuplicateGroup(canonicalId: string): void {
    clearSelection();
    setQuery("");
    setSearch("");
    setSemantic(false);
    setStatus("all");
    setDuplicateScope("all");
    setCanonicalWorkItemId(canonicalId);
  }

  async function merged(result: WorkMergeResult): Promise<void> {
    setMergeOpen(false);
    setRefresh((value) => value + 1);
    setAttentionRefresh((value) => value + 1);
    setCheckpointRefresh((value) => value + 1);
    setEventRefresh((value) => value + 1);
    // The exact audit load below replaces the open context; a separate refresh would race it.
    const auditLoaded = await openExactWork(
      result.merge.project_id,
      result.merge.source_work_item_id
    );
    setNotice(auditLoaded
      ? { message: "Merge recorded. The exact source audit is open; canonical lists are refreshing." }
      : {
        message: "Merge recorded, but the exact source audit could not be loaded. Retry the audit load before taking further action.",
        error: true
      });
  }

  async function mergeSourceChanged(): Promise<void> {
    const changedSource = context?.work_item ?? openedRef.current?.work_item;
    setMergeOpen(false);
    if (changedSource) await openExactWork(changedSource.project_id, changedSource.id);
  }

  function unsavedEditsKept(): boolean {
    if (mode !== "edit" || !context || !editDraft) return false;
    if (JSON.stringify(editDraft) === JSON.stringify(draftFromWork(context.work_item))) return false;
    return !window.confirm("Discard your unsaved work-item edits?");
  }

  function leavingOpenedWorkAllowed(): boolean {
    if (editSaving || checkpointSaving) return false;
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
      return false;
    }
    if (
      (checkpointBody || checkpointBranch || checkpointCommit
        || checkpointAffectedPaths || checkpointTags
        || !completionEvidenceDraftIsEmpty(completionEvidenceDraft))
      && !window.confirm("Discard your unsaved checkpoint?")
    ) return false;
    if (unsavedEditsKept()) return false;
    return true;
  }

  function closeWork() {
    if (!leavingOpenedWorkAllowed()) return;
    clearSelection();
  }

  // Escape closes the pane the way its Back button does, including the unsaved-draft
  // prompt. With nothing open it stays silent rather than raising that prompt.
  function deselectWork(): void {
    if (selectedWorkItemId() === null) return;
    closeWork();
  }

  // The pane's record, including one whose exact load has not landed yet: an in-flight
  // load would otherwise open into the pane after the queue had already moved on.
  function selectedWorkItemId(): string | null {
    return openedId ?? exactContextTarget.current?.workItemId ?? null;
  }

  // A lifecycle filter names a different queue, so the pane's record may no longer belong
  // to it; the selection is dropped rather than left stranded beside the new list.
  function filterByStatus(next: StatusFilter): void {
    const transition = statusFilterTransition(status, next, selectedWorkItemId());
    if (transition === "unchanged") return;
    const deselecting = transition === "refilter-and-deselect";
    if (deselecting && !leavingOpenedWorkAllowed()) return;
    // Both changes land inside one cross-dissolve, so the queue and the pane it retires
    // are captured together rather than swapping out from under each other.
    crossfade.run(paneCrossfadeTargets(transition), () => {
      if (deselecting) clearSelection();
      setStatus(next);
    });
  }

  // Clearing filters returns the queue to Pending, so it drops the selection on the
  // same rule the lifecycle buttons follow instead of stranding it beside the reset list.
  function clearFilters(): void {
    const transition = statusFilterTransition(status, "pending", selectedWorkItemId());
    const deselecting = transition === "refilter-and-deselect";
    if (deselecting && !leavingOpenedWorkAllowed()) return;
    // Clearing renames the queue's query even when the lifecycle filter was already
    // Pending, so the queue cross-dissolves regardless of the lifecycle transition.
    crossfade.run(paneCrossfadeTargets(transition, true), () => {
      if (deselecting) clearSelection();
      setQuery("");
      setSearch("");
      setDuplicateScope("canonical");
      setCanonicalWorkItemId("");
      setStatus("pending");
      setTagInput("");
      setSourceClientInput("");
      setSourceSessionInput("");
      setTagFilter("");
      setSourceClientFilter("");
      setSourceSessionFilter("");
    });
  }

  function selectWork(summary: WorkSummary) {
    if (opened?.work_item.id === summary.work_item.id) return;
    if (!leavingOpenedWorkAllowed()) return;
    setMergeOpen(false);
    openWork(summary);
  }

  function retryOpenedContext(): void {
    const target = exactContextTarget.current;
    if (target) {
      void openExactWork(target.projectId, target.workItemId);
    } else if (opened) {
      void loadContext(opened);
    }
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

  function startEdit(): void {
    if (!context) return;
    if (context.canonical.is_duplicate) {
      setNotice({
        message: "Duplicate audit records are immutable. Open the record to review its canonical destination.",
        error: true
      });
      return;
    }
    if (mutationRegistry.blocks([
      mutationWorkKey(context.work_item.project_id, context.work_item.id)
    ])) {
      setNotice({
        message: "Resolve the pending mutation for this work item before editing it.",
        error: true
      });
      return;
    }
    setEditDraft(draftFromWork(context.work_item));
    setEditError("");
    setConflict(null);
    setTab("context");
    setMergeOpen(false);
    setMode("edit");
  }

  function cancelEdit(): void {
    if (detailMutationBlocked) return;
    setMode("view");
    if (context) setEditDraft(draftFromWork(context.work_item));
    setEditError("");
    setConflict(null);
  }

  function openMerge(): void {
    if (!context || context.canonical.is_duplicate) return;
    if (unsavedEditsKept()) return;
    setTab("graph");
    setMode("view");
    setMergeOpen(true);
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
      const value = await api<unknown>(`${workItemPath(context.work_item.project_id, context.work_item.id)}/context?recent_limit=5&recent_event_limit=10`);
      const latest = decodeWorkContext(
        value,
        context.work_item.project_id,
        context.work_item.id
      );
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
    const evidenceIssues = complete
      ? completionEvidenceDraftIssues(completionEvidenceDraft)
      : [];
    if (evidenceIssues.length > 0) {
      setCompletionEvidenceIssues(evidenceIssues);
      setCheckpointActionError("Review the highlighted completion evidence before completing this work.");
      return;
    }
    const completionEvidence = complete
      ? completionEvidenceFromDraft(completionEvidenceDraft)
      : null;
    setCheckpointSaving(true);
    setCheckpointActionError("");
    setCheckpointAffectedPathsError("");
    try {
      const checkpoint = checkpointPayload(
        checkpointBody,
        checkpointBranch,
        checkpointCommit,
        checkpointTags,
        checkpointAffectedPaths
      );
      const base = workItemPath(context.work_item.project_id, context.work_item.id);
      if (complete) {
        await mutationRegistry.execute({
          kind: "complete_work",
          slot: `complete-work:${context.work_item.project_id}:${context.work_item.id}`,
          projectId: context.work_item.project_id,
          conflictKeys: [
            mutationWorkKey(context.work_item.project_id, context.work_item.id)
          ],
          method: "POST",
          path: `${base}/complete`,
          payload: {
            expected_version: context.work_item.version,
            checkpoint,
            ...(completionEvidence ? { completion_evidence: completionEvidence } : {})
          }
        });
        setNotice({ message: "Completion checkpoint recorded and work marked done." });
        setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
        setCompletionEvidenceIssues([]);
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
      setCheckpointAffectedPaths("");
      setCheckpointAffectedPathsError("");
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
      if (error instanceof AffectedPathsValidationError) {
        setCheckpointAffectedPathsError(error.message);
      } else if (complete && isVersionConflict(error)) {
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
      if (opened?.work_item.id === deleteTarget.id) clearSelection();
      setDeleteTarget(null);
      setRefresh((value) => value + 1);
      setNotice({ message: "Work item removed from ordinary project views. Its history remains recoverable." });
    } catch (error) {
      if (isVersionConflict(error)) {
        try {
          const value = await api<unknown>(workItemPath(deleteTarget.project_id, deleteTarget.id));
          const latest = decodeWorkItemDetail(
            value,
            deleteTarget.project_id,
            deleteTarget.id
          );
          setDeleteTarget(latest.work_item);
          setDeleteError(`This work item changed. Deletion was not retried; review the current version ${latest.work_item.version} before trying again.`);
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
    if (
      deferringId
      || summary.readiness.is_duplicate
      || (work.status !== "pending" && work.status !== "deferred")
    ) return;
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
        setNotice({
          message: summary.readiness.is_gated
            ? `“${work.title}” is Pending but still needs human attention, so it remains out of ready discovery.`
            : summary.readiness.is_blocked
              ? `“${work.title}” is Pending but still blocked, so it remains out of ready discovery.`
              : `“${work.title}” is Pending and available in the work queue.`
        });
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
      if (openedRef.current?.work_item.id === work.id) void reloadOpenContext();
    } catch (error) {
      if (isVersionConflict(error)) setRefresh((value) => value + 1);
      setNotice({ message: errorMessage(error), error: true });
    } finally {
      setDeferringId(null);
    }
  }

  function openDeletion(item: WorkItem) {
    if (context?.work_item.id === item.id && context.canonical.is_duplicate) {
      setNotice({ message: "Duplicate audit records are immutable.", error: true });
      return;
    }
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
      if (intent.kind === "merge_work") {
        const result = await mutationRegistry.retry<"merge_work">(intent.slot);
        const selected = openedRef.current?.work_item;
        if (selected?.id === result.merge.source_work_item_id
          && selected.project_id === result.merge.project_id) {
          await merged(result);
          return;
        }
      } else {
        await mutationRegistry.retry(intent.slot);
      }
      setRefresh((value) => value + 1);
      setAttentionRefresh((value) => value + 1);
      setCheckpointRefresh((value) => value + 1);
      setEventRefresh((value) => value + 1);
      setContextRefresh((value) => value + 1);
      if (intent.kind === "create_work") setWorkDialog("closed");
      if (intent.kind === "delete_work") {
        setDeleteTarget(null);
        if (opened && intent.conflictKeys.includes(
          mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
        )) clearSelection();
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
        setCheckpointAffectedPaths("");
        setCheckpointAffectedPathsError("");
        setCheckpointTags("");
        if (intent.kind === "complete_work") {
          setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
          setCompletionEvidenceIssues([]);
        }
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
      setAttentionRefresh((value) => value + 1);
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

  // c copies the open record's recall pointer: the same value, notice, and copied
  // state the record's own button produces, so the two cannot drift.
  function copyOpenedRecallPointer(): void {
    if (opened) copyRecallPointer(opened);
  }

  async function copyProjectId() {
    if (project) await copyText(project.id, "project", `Project ID copied: ${project.id}`);
  }

  function blockNavigationWhilePending(event: MouseEvent<HTMLAnchorElement>): void {
    if (!activeProjectMutationBlocked) return;
    event.preventDefault();
    setNotice({
      message: "Resolve pending mutations before leaving this dashboard document.",
      error: true
    });
  }

  function openAttentionWork(summary: WorkSummary): void {
    if (activeProjectMutationBlocked) {
      setNotice({
        message: "Resolve pending mutations before leaving this dashboard document.",
        error: true
      });
      return;
    }
    window.location.assign(`/?work=${encodeURIComponent(summary.work_item.id)}`);
  }

  async function afterWorkMutation(
    failureMessage: string,
    { refreshAttention = false, refreshEvents = false } = {}
  ): Promise<boolean> {
    if (refreshAttention) setAttentionRefresh((value) => value + 1);
    setRefresh((value) => value + 1);
    if (refreshEvents) setEventRefresh((value) => value + 1);
    const reconciled = await reloadOpenContext();
    if (!reconciled) setNotice({ message: failureMessage, error: true });
    return reconciled;
  }

  const activeProjectMutationBlocked = Boolean(
    activeId && selectMutationScope(mutationIntents, { projectId: activeId }).blocked
  );
  const openedWorkKey = opened
    ? mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
    : null;
  const openedPaneVisible = view === "library" && !projectsError && projects.length > 0
    && opened !== null;
  const openedWorkMutationBlocked = Boolean(
    openedWorkKey && selectMutationScope(mutationIntents, { conflictKeys: [openedWorkKey] }).blocked
  );
  const detailMutationBlocked = openedWorkMutationBlocked
    || contextReconciliationRequired && contextLoading;
  const createWorkMutationBlocked = Boolean(
    project && selectMutationScope(mutationIntents, {
      conflictKeys: [mutationCreateKey(project.id)]
    }).blocked
  );
  const {
    createDialog: createDialogMutationIntents,
    deleteDialog: deleteDialogMutationIntents,
    mergePanel: mergePanelMutationIntents,
    openedPane: openedPaneMutationIntents,
    global: globalMutationIntents
  } = selectMutationRecovery(mutationIntents, {
    createWorkKey: workDialog !== "closed" && project ? mutationCreateKey(project.id) : undefined,
    deleteWorkKey: deleteTarget
      ? mutationWorkKey(deleteTarget.project_id, deleteTarget.id)
      : undefined,
    // The graph owns recovery only while its exact source panel is visible and interactive.
    // Other tabs and unavailable/reconciling context use the always-visible pane header.
    mergeSlot: opened && openedPaneVisible && mergeOpen && tab === "graph"
      && context?.work_item.id === opened.work_item.id
      && !(contextReconciliationRequired && contextLoading)
      ? `merge-work:${opened.work_item.project_id}:${opened.work_item.id}`
      : undefined,
    openedWorkKey: openedPaneVisible ? openedWorkKey ?? undefined : undefined
  });
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
    <a className="skip-link" href="#main-content">{view === "settings" ? "Skip to project settings" : view === "attention" ? "Skip to human questions" : "Skip to work items"}</a>
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Mnemonic home" aria-disabled={activeProjectMutationBlocked || undefined} onClick={blockNavigationWhilePending}><Logo /><span>mnemonic<span className="brand-period">.</span></span></a>
      <div className="workspace-picker">
        <label className="section-label" htmlFor="project-select">YOUR WORKSPACE</label>
        <div className="select-wrap"><select id="project-select" aria-keyshortcuts="1 2 3 4 5 6 7 8 9 0" value={activeId} disabled={projectsLoading || !projects.length || activeProjectMutationBlocked} onChange={(event) => chooseProject(event.target.value)}>
          {!projects.length && <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>}
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><span className="select-chevron" aria-hidden="true">⌄</span></div>
        <button className="new-project-button" type="button" disabled={projectsLoading || activeProjectMutationBlocked} onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={15} />New project</button>
        {project && <button className="copy-project-button" type="button" title={`Project ID: ${project.id}`} onClick={() => void copyProjectId()}><Icon name="copy" size={13} />Copy project ID for your agent</button>}
      </div>
      <nav aria-label="Workspace navigation">
        <a className={`nav-item ${view === "library" ? "active" : ""}`} href="/" aria-current={view === "library" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="library" /><span>Work library</span><Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "attention" ? "active" : ""}`} href="/attention" aria-current={view === "attention" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="attention" /><span>Needs Attention</span>{attentionCount !== null && <span className="attention-nav-count" aria-label={`${attentionCount} unresolved human question${attentionCount === 1 ? "" : "s"}`}>{attentionCount}</span>}<Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "settings" ? "active" : ""}`} href="/settings" aria-current={view === "settings" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="settings" /><span>Project settings</span><Icon name="arrow" size={15} /></a>
      </nav>
      <div className="sidebar-note"><img className="note-art" src="/img/robot.svg" alt="" width={115} height={115} aria-hidden="true" /><h2>Keep your agents on the same page.</h2><p>Work units are reserved and nothing is forgotten.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /><span>Local workspace</span><ThemeSelector /></div>
    </aside>

    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><span>{project?.name || "Getting started"}</span>{view !== "library" && <><span className="breadcrumb-slash">/</span><span>{view === "settings" ? "Project settings" : "Needs Attention"}</span></>}</div><span className="topbar-note"><span className="small-mark">m.</span>Context worth keeping</span></header>
      <div className={`page-content ${view === "library" ? "page-content-library" : ""}`}>
        {view === "settings" ? <>
          <DashboardViewChrome
            eyebrow="PROJECT CONFIGURATION"
            title="Project settings"
            description={project ? `Control how Mnemonic hands off work from “${project.name}”.` : "Choose a project, then configure how Mnemonic hands off its work."}
            liveSyncStatus={liveSyncStatus}
            onRefresh={() => {
              setProjectsRefresh((value) => value + 1);
              setSettingsRefresh((value) => value + 1);
            }}
          />
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
        </> : view === "attention" ? <>
          <DashboardViewChrome
            eyebrow="EXPLICIT HUMAN OVERSIGHT"
            title="Needs Attention"
            description={project ? `Durable questions waiting in “${project.name}”.` : "Choose a project to review its explicit human questions."}
            liveSyncStatus={liveSyncStatus}
            onRefresh={() => {
              setProjectsRefresh((value) => value + 1);
              setAttentionRefresh((value) => value + 1);
              setContextRefresh((value) => value + 1);
              setEventRefresh((value) => value + 1);
            }}
          />
          {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> :
            projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> :
            <HumanAttentionList
              project={project}
              refreshSignal={attentionRefresh}
              onOpen={openAttentionWork}
              onResolved={() => {
                setRefresh((value) => value + 1);
                setContextRefresh((value) => value + 1);
                setEventRefresh((value) => value + 1);
              }}
            />}
        </> : <>
          <DashboardViewChrome
            eyebrow="DURABLE WORK FOR TEMPORARY SESSIONS"
            title="Work library"
            subject={project?.name}
            description={project?.description || "One objective. Many immutable checkpoints. Ready for whoever continues it."}
            liveSyncStatus={liveSyncStatus}
            onRefresh={() => {
              setProjectsRefresh((value) => value + 1);
              setRefresh((value) => value + 1);
              setCheckpointRefresh((value) => value + 1);
              setEventRefresh((value) => value + 1);
              setContextRefresh((value) => value + 1);
            }}
            actions={project && <button className="button button-primary" type="button" disabled={createWorkMutationBlocked} onClick={openWorkDialog}><Icon name="plus" size={16} />New work</button>}
          />

          {projectsError ? <ErrorNotice message={projectsError}><button className="button button-secondary" onClick={() => setProjectsRefresh((value) => value + 1)}>Try again</button></ErrorNotice> :
            projectsLoading && !projects.length ? <div className="loading-state" role="status"><span className="spinner" />Opening your workspace…</div> :
            !projects.length ? <section className="empty-state onboarding"><div className="empty-art"><Icon name="library" size={34} /><span /></div><div className="eyebrow">A DURABLE PLACE TO CONTINUE</div><h2>Create your first project.</h2><p>Projects hold stable objectives and the session checkpoints that move them forward.</p><button className="button button-primary" onClick={() => setProjectDialog(true)}><Icon name="plus" size={17} />Create your first project</button></section> : <>
              <WorkItemList
                queuePaneRef={crossfade.queueRef}
                query={query}
                searchedQuery={search}
                searchRef={searchRef}
                semantic={semantic}
                duplicateScope={duplicateScope}
                canonicalWorkItemId={canonicalWorkItemId}
                status={status}
                sort={sort}
                tag={tagInput}
                sourceClient={sourceClientInput}
                sourceSessionId={sourceSessionInput}
                items={queue.items}
                flatSearch={queue.flatSearch}
                total={queue.total}
                loading={queue.loading}
                refreshing={queue.refreshing}
                appending={queue.appending}
                error={queue.error}
                appendError={queue.appendError}
                hasMore={queue.hasMore}
                refreshKey={refresh}
                viewKey={queue.viewKey}
                selectedId={openedId}
                copiedKey={copied}
                onQuery={setQuery}
                onToggleSemantic={() => setSemantic((value) => !value)}
                onDuplicateScope={(value) => {
                  setDuplicateScope(value);
                  if (value === "canonical") setCanonicalWorkItemId("");
                }}
                onClearDuplicateGroup={() => setCanonicalWorkItemId("")}
                onStatus={filterByStatus}
                onSort={setSort}
                onTag={setTagInput}
                onSourceClient={setSourceClientInput}
                onSourceSessionId={setSourceSessionInput}
                onRetry={queue.retry}
                onRetryAppend={queue.retryAppend}
                onLoadMore={queue.loadMore}
                onClearFilters={clearFilters}
                onCreate={openWorkDialog}
                onSelect={selectWork}
                onDeselect={deselectWork}
                onCopySelectedPointer={copyOpenedRecallPointer}
                onCopyPointer={(item) => void copyRecallPointer(item)}
                detail={<WorkDetailPane
                  paneRef={crossfade.detailRef}
                  opened={opened}
                  context={context}
                  contextLoading={contextLoading}
                  contextError={contextError}
                  reconciliationRequired={contextReconciliationRequired}
                  onRetryContext={retryOpenedContext}
                  tab={tab}
                  onTab={setTab}
                  mode={mode}
                  editDraft={editDraft}
                  editSaving={editSaving}
                  mutationBlocked={detailMutationBlocked}
                  editError={editError}
                  conflict={conflict}
                  setEditDraft={(updater) => setEditDraft((draft) => draft ? updater(draft) : draft)}
                  onSaveEdits={(event) => void saveWorkEdits(event)}
                  onCancelEdit={cancelEdit}
                  onLoadCurrent={() => void loadLatestWork()}
                  onUseCurrentVersion={useCurrentVersion}
                  onEdit={startEdit}
                  mergeOpen={mergeOpen}
                  mergeRecoveryVisible={mergePanelMutationIntents.length > 0}
                  onOpenMerge={openMerge}
                  onCloseMerge={() => setMergeOpen(false)}
                  onMerged={merged}
                  onMergeSourceChanged={mergeSourceChanged}
                  onDelete={() => { if (context) openDeletion(context.work_item); }}
                  onDefer={(item) => void toggleDeferral(item)}
                  deferring={Boolean(openedId && deferringId === openedId)}
                  onOpenCanonical={(workItemId) => {
                    if (opened) void openExactWork(opened.work_item.project_id, workItemId);
                  }}
                  onViewDuplicateGroup={viewDuplicateGroup}
                  onCopy={(value, key, success) => void copyText(value, key, success)}
                  onCopyPointer={(item) => void copyRecallPointer(item)}
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
                  checkpointAffectedPaths={checkpointAffectedPaths}
                  checkpointAffectedPathsError={checkpointAffectedPathsError}
                  checkpointTags={checkpointTags}
                  checkpointSaving={checkpointSaving}
                  completionEvidenceDraft={completionEvidenceDraft}
                  completionEvidenceIssues={completionEvidenceIssues}
                  evidenceRefreshSignal={eventRefresh}
                  onCheckpointKind={setCheckpointKind}
                  onCheckpointBody={setCheckpointBody}
                  onCheckpointBranch={setCheckpointBranch}
                  onCheckpointCommit={setCheckpointCommit}
                  onCheckpointAffectedPaths={(value) => {
                    setCheckpointAffectedPaths(value);
                    setCheckpointAffectedPathsError("");
                  }}
                  onCheckpointTags={setCheckpointTags}
                  onCompletionEvidenceDraft={(draft) => {
                    setCompletionEvidenceDraft(draft);
                    setCompletionEvidenceIssues([]);
                    setCheckpointActionError("");
                  }}
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
                  onEventAppended={() => afterWorkMutation(
                    "Progress was saved, but current work context could not be reloaded. Use Refresh before continuing."
                  )}
                  onGateResolved={async () => {
                    await afterWorkMutation(
                      "The answer was recorded, but current work context could not be reloaded. Use Refresh before continuing.",
                      { refreshAttention: true, refreshEvents: true }
                    );
                  }}
                  eventRefreshSignal={eventRefresh}
                  recovery={modalRecovery(openedPaneMutationIntents)}
                  notice={workDialog === "suspended"
                    ? <div className="detail-notice" role="status"><span>Inspecting existing work for your unsaved draft.</span><button type="button" className="button button-secondary" onClick={() => setWorkDialog("open")}>Return to new work</button></div>
                    : undefined}
                  onBack={closeWork}
                  backDisabled={editSaving || checkpointSaving || detailMutationBlocked}
                />}
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

    {workDialog !== "closed" && project && <Dialog title="Create durable work" onClose={() => { if (!workSaving && !createWorkMutationBlocked) { setCreateAffectedPathsError(""); setWorkDialog("closed"); } }} recovery={modalRecovery(createDialogMutationIntents)} wide busy={workSaving || createWorkMutationBlocked} suspended={workDialog === "suspended"}><form className="form-stack" onSubmit={(event) => void createWork(event)}>
      <p className="dialog-intro">The objective remains editable. Its initial checkpoint is immutable and attributed to this dashboard session.</p>
      <label className="field">Title<input name="title" required disabled={createWorkMutationBlocked} maxLength={200} autoFocus placeholder="What durable objective should survive this session?" onInput={() => setSuggestionDraftGeneration((value) => value + 1)} /></label>
      <label className="field">Summary<textarea name="summary" required disabled={createWorkMutationBlocked} rows={3} maxLength={1000} placeholder="When is this work relevant?" onInput={() => setSuggestionDraftGeneration((value) => value + 1)} /></label>
      <label className="field field-half">Priority<input name="priority" type="number" disabled={createWorkMutationBlocked} min={0} max={100} defaultValue={0} /></label>
      <label className="field">Initial context checkpoint<textarea className="prompt-editor" name="prompt" required disabled={createWorkMutationBlocked} rows={14} maxLength={100000} spellCheck={false} placeholder="Context, intended outcome, references, hazards, and verification…" onInput={() => setSuggestionDraftGeneration((value) => value + 1)} /><span className="field-hint">Saved exactly as entered. Corrections become new checkpoints.</span></label>
      <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input name="repository_branch" disabled={createWorkMutationBlocked} maxLength={200} /></label><label className="field">Caller-asserted baseline commit<input name="verified_against" disabled={createWorkMutationBlocked} className="mono" maxLength={64} /></label><AffectedPathsEditor name="affected_paths" disabled={createWorkMutationBlocked} error={createAffectedPathsError} onChange={() => setCreateAffectedPathsError("")} /><label className="field">Tags <span className="optional">Comma separated</span><input name="tags" disabled={createWorkMutationBlocked} onInput={() => setSuggestionDraftGeneration((value) => value + 1)} /></label></div></details>
      <DuplicateSuggestionPanel
        projectId={project.id}
        draftGeneration={suggestionDraftGeneration}
        disabled={createWorkMutationBlocked}
        onInspect={(workItemId) => { void inspectExistingWork(project.id, workItemId); }}
      />
      {newWorkError && <ErrorNotice message={newWorkError} />}
      <div className="dialog-actions"><button type="button" className="button button-secondary" disabled={workSaving || createWorkMutationBlocked} onClick={() => { setCreateAffectedPathsError(""); setWorkDialog("closed"); }}>Cancel</button><button type="submit" className="button button-primary" disabled={workSaving || createWorkMutationBlocked}>{workSaving ? "Creating…" : "Create work and checkpoint"}</button></div>
    </form></Dialog>}
    {deleteTarget && <Dialog title="Delete this work item?" onClose={() => { if (!deleting && !mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])) setDeleteTarget(null); }} recovery={modalRecovery(deleteDialogMutationIntents)} busy={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])}>
      <p className="dialog-intro">This hides the objective and all checkpoints from ordinary reads. Immutable history remains recoverable in the database.</p>
      <div className="delete-preview"><StatusBadge status={deleteTarget.status} /><h3>{deleteTarget.title}</h3><p>{deleteTarget.summary}</p><span>Version {deleteTarget.version} · {formatDate(deleteTarget.updated_at)}</span></div>
      {deleteError && <ErrorNotice message={deleteError} />}
      <div className="dialog-actions"><button className="button button-secondary" disabled={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])} onClick={() => setDeleteTarget(null)}>Keep work item</button><button className="button button-danger" disabled={deleting || mutationRegistry.blocks([mutationWorkKey(deleteTarget.project_id, deleteTarget.id)])} onClick={() => void deleteWork()}>{deleting ? "Working…" : "Delete work item"}</button></div>
    </Dialog>}
  </div></MutationIntentProvider>;
}
