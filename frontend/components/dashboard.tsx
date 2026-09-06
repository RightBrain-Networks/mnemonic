"use client";

import ExternalReferencesEditor from "@/components/external-references-editor";
import CodeReviewHandoffEditor, { emptyReviewHandoff } from "@/components/code-review-handoff-editor";
import CodeReviewInbox from "@/components/code-review-inbox";
import JobReportEditor from "@/components/job-report-editor";
import { codeReviewDecision } from "@/lib/code-review-policy";
import { decodeCodeReviewDetail, validReviewHandoff, type CodeReviewHandoff } from "@/lib/code-reviews";
import { coldReviewPrompt, warmReviewDirective } from "@/lib/code-review-prompts";
import { normalizeExternalReferences, sameExternalReferences } from "@/lib/external-references";
import type { ExternalReference } from "@/lib/types";
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
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import JobReportList from "@/components/job-report-list";
import { useProjectActivity } from "@/components/use-project-activity";
import { decodeProjectSettings, decodeReportCount, emptyJobReportDraft, jobReportFromDraft, jobReportDraftHasEdits, type JobReportDraft } from "@/lib/job-completion-reports";
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
  dashboardLibraryToolsPreference,
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
  DashboardWorkActivationInput,
  DashboardWorkPendingInput,
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
  WorkMoveInput,
  WorkMoveResult,
  WorkPatch,
  WorkSort,
  WorkSummary
} from "@/lib/types";
import { sameUuid, validUuid } from "@/lib/wire-guards";
import type { DetailTab } from "@/lib/work-detail-tabs";
import { editableLifecycleStatuses, normalizedTags } from "@/lib/work-item-view";
import { paneCrossfadeTargets } from "@/lib/pane-crossfade";
import { dashboardMutationActor } from "@/lib/work-events";
import {
  decodeDashboardActivationResult,
  decodeLeaseReleaseResult,
  humanDecisionCompletionCheckpoint,
  humanDecisionReport,
  statusActionDisabledReason,
  type ManualStatusAction
} from "@/lib/work-status-actions";
import { scheduleHierarchyFilterCommit } from "@/lib/work-item-search";
import { statusFilterLabels, statusFilterTransition } from "@/lib/work-queue";
import { workRecallPointer } from "@/lib/work-recall-pointer";
import {
  loadCompleteProjectCatalog,
  preservedWorkMoveDisplayStatus,
  resolveCurrentWorkProject,
  sameProjectCatalog,
  summaryAfterWorkMove,
  workMoveDisabledReason,
  type WorkMoveDisplayStatus
} from "@/lib/work-move";
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

function summaryFromContext(context: WorkContext): WorkSummary {
  return {
    work_item: context.work_item,
    checkpoint_count: context.checkpoint_total,
    ancestor_path: [],
    ancestor_path_truncated: false,
    current_context: currentContext(context),
    readiness: context.readiness
  };
}

function isDefinitiveWorkPlacementMiss(error: unknown): error is ApiError {
  return error instanceof ApiError
    && error.status === 404
    && (error.code === "work_item_not_found" || error.code === "project_not_found");
}

function locationWorkSelection(): string | null {
  const value = new URLSearchParams(window.location.search).get("work");
  return value && validUuid(value) ? value : null;
}

type ContextLoadResult = "loaded" | "superseded" | "failed";
type WorkPlacementRecoveryResult = "relocated" | "absent" | "ambiguous" | "superseded";
type WorkPlacementRecoveryReason = "context_refresh" | "move_action";
type WorkPlacementSummary = Pick<WorkSummary, "work_item">;
type ExactContextTarget = {
  projectId: string;
  workItemId: string;
  moveRecovery?: WorkPlacementSummary;
};
type WorkContextScanResult =
  | { kind: "found"; project: Project; context: WorkContext }
  | { kind: "absent" }
  | { kind: "superseded" };
type WorkDialogState = "closed" | "open" | "suspended";

export default function Dashboard({ view = "library", timeZone }: { view?: "library" | "attention" | "summaries" | "settings"; timeZone?: string | null; }) {
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
  const [settingsFetching, setSettingsFetching] = useState(false);
  const [settingsLoadError, setSettingsLoadError] = useState("");
  const [settingsRefresh, setSettingsRefresh] = useState(0);

  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [libraryToolsOpen, setLibraryToolsOpen] = useState(true);
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
  const [reportRefresh, setReportRefresh] = useState(0);
  const [reportCount, setReportCount] = useState<string | null>(null);
  const [activityReadyProjectId, setActivityReadyProjectId] = useState("");
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
  const [createExternalReferences, setCreateExternalReferences] = useState<ExternalReference[]>([]);
  const [editDraft, setEditDraft] = useState<WorkEditDraft | null>(null);
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const [conflict, setConflict] = useState<WorkItem | null>(null);
  const recordRequest = useRef(0);
  const workPlacementRequest = useRef(0);
  const lastLoadedContextRequest = useRef(0);
  const exactContextTarget = useRef<ExactContextTarget | null>(null);
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
  const [jobReportDraft, setJobReportDraft] = useState<JobReportDraft>(emptyJobReportDraft);
  const [reviewCloseout, setReviewCloseout] = useState<{ mode: "checkpoint" | "manual"; summary: WorkSummary; revision: string } | null>(null);
  const [reviewHandoff, setReviewHandoff] = useState<CodeReviewHandoff>(emptyReviewHandoff);
  const reviewDraftWorkId = useRef<string | null>(null);
  const [reviewCloseoutError, setReviewCloseoutError] = useState("");
  const [reopenReview, setReopenReview] = useState<WorkContext | null>(null);
  const [reviewReopening, setReviewReopening] = useState(false);
  const [reviewReopenError, setReviewReopenError] = useState("");
  const [completionEvidenceIssues, setCompletionEvidenceIssues] =
    useState<readonly CompletionEvidenceIssue[]>([]);

  const [deleteTarget, setDeleteTarget] = useState<WorkItem | null>(null);
  const [movingId, setMovingId] = useState<string | null>(null);
  const [statusChangingId, setStatusChangingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ message: string; error?: boolean } | null>(null);
  const [liveSyncStatus, setLiveSyncStatus] = useState<LiveSyncStatus>("connecting");
  const project = projects.find((item) => item.id === activeId);
  const queue = useWorkQueuePages({
    enabled: view === "library" && Boolean(activeId) && activityReadyProjectId === activeId && preferencesReady,
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
  const activity = useProjectActivity({
    projectId: activeId,
    onBootstrap: setActivityReadyProjectId,
    onInvalidation: (changes) => {
      if (changes.work) { setRefresh((value) => value + 1); setAttentionRefresh((value) => value + 1); setContextRefresh((value) => value + 1); setCheckpointRefresh((value) => value + 1); setEventRefresh((value) => value + 1); }
      if (changes.reports) setReportRefresh((value) => value + 1);
      if (changes.settings) setSettingsRefresh((value) => value + 1);
      if (changes.projects) setProjectsRefresh((value) => value + 1);
    },
    onRetryDirty: () => {
      if (queue.error) queue.retry();
      if (settingsLoadError) setSettingsRefresh((value) => value + 1);
      if (contextError) setContextRefresh((value) => value + 1);
      if (projectsError) setProjectsRefresh((value) => value + 1);
      if (attentionCount === null) setAttentionRefresh((value) => value + 1);
      if (reportCount === null) setReportRefresh((value) => value + 1);
    }
  });
  const activityPoll = useRef(activity.poll);
  activityPoll.current = activity.poll;
  const crossfade = usePaneCrossfade();
  const projectCatalogRequest = useRef(0);
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
  useEffect(() => { setJobReportDraft(emptyJobReportDraft()); }, [openedId]);

  useEffect(() => {
    try {
      const storedLibraryToolsOpen = dashboardLibraryToolsPreference(
        localStorage.getItem(dashboardStorageKeys.libraryTools)
      );
      document.documentElement.dataset.libraryTools = storedLibraryToolsOpen ? "open" : "closed";
      setLibraryToolsOpen(storedLibraryToolsOpen);
      setStatus(dashboardStatusPreference(localStorage.getItem(dashboardStorageKeys.status)));
      setSort(dashboardSortPreference(localStorage.getItem(dashboardStorageKeys.sort)));
    } catch {
      // Preferences are optional when storage is unavailable.
    }
    setPreferencesReady(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const requestId = ++projectCatalogRequest.current;
    const isCurrent = () =>
      !controller.signal.aborted && projectCatalogRequest.current === requestId;
    setProjectsLoading(true);
    setProjectsError("");
    async function load() {
      const all = await fetchFreshProjectCatalog(
        isCurrent,
        controller.signal
      );
      if (!all || !isCurrent()) return;
      all.sort((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
      setProjects(all);
      let saved = "";
      try { saved = localStorage.getItem(dashboardStorageKeys.project) ?? ""; } catch { /* optional */ }
      setActiveId((current) => all.some((item) => item.id === current) ? current : all.find((item) => item.id === saved)?.id ?? all[0]?.id ?? "");
    }
    load().catch((error) => { if (isCurrent()) setProjectsError(errorMessage(error)); })
      .finally(() => { if (isCurrent()) setProjectsLoading(false); });
    return () => controller.abort();
  }, [projectsRefresh]);

  useEffect(() => {
    if (!activeId) return;
    try { localStorage.setItem(dashboardStorageKeys.project, activeId); } catch { /* optional */ }
  }, [activeId]);

  useEffect(() => {
    if (!activeId || activityReadyProjectId !== activeId) {
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
  }, [activeId, activityReadyProjectId, attentionRefresh]);

  useEffect(() => {
    setReportCount(null);
    if (!activeId || activityReadyProjectId !== activeId) return;
    const controller = new AbortController();
    api<unknown>(`/projects/${activeId}/job-completion-reports/count`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setReportCount(decodeReportCount(value, activeId).undismissed_count); })
      .catch(() => { if (!controller.signal.aborted) setReportCount(null); });
    return () => controller.abort();
  }, [activeId, activityReadyProjectId, reportRefresh]);

  useEffect(() => {
    if (!preferencesReady) return;
    try {
      localStorage.setItem(
        dashboardStorageKeys.libraryTools,
        libraryToolsOpen ? "open" : "closed"
      );
      localStorage.setItem(dashboardStorageKeys.status, status);
      localStorage.setItem(dashboardStorageKeys.sort, sort);
    } catch {
      // Preferences are optional when storage is unavailable.
    }
  }, [libraryToolsOpen, preferencesReady, sort, status]);

  useEffect(() => {
    const generation = ++settingsLoadGeneration.current;
    settingsLoadController.current?.abort();
    settingsLoadController.current = null;
    if (!activeId || activityReadyProjectId !== activeId) {
      setProjectSettings(null);
      setSettingsLoading(false);
      setSettingsFetching(false);
      setSettingsLoadError("");
      return;
    }
    const controller = new AbortController();
    settingsLoadController.current = controller;
    const blockingLoad = isBlockingProjectSettingsLoad(activeId, projectSettings);
    setProjectSettings((current) => current?.project_id === activeId ? current : null);
    setSettingsLoading(blockingLoad);
    setSettingsFetching(true);
    setSettingsLoadError("");
    api<unknown>(`/projects/${encodeURIComponent(activeId)}/settings`, {
      signal: controller.signal
    })
      .then((loaded) => {
        if (!isCurrentProjectSettingsLoad(
          generation,
          settingsLoadGeneration.current,
          controller.signal.aborted
        )) return;
        setProjectSettings(decodeProjectSettings(loaded, activeId));
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
        setSettingsFetching(false);
      });
    return () => {
      controller.abort();
      if (settingsLoadController.current === controller) {
        settingsLoadController.current = null;
      }
    };
  }, [activeId, activityReadyProjectId, settingsRefresh]);

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
      activityPoll.current();
      setReportRefresh((value) => value + 1);
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
    if (contextLoading || mutationRegistry.blocks([
      mutationWorkKey(opened.work_item.project_id, opened.work_item.id)
    ])) return;
    lastContextRefresh.current = contextRefresh;
    // This refresh preserves the selected record, so it must not advance the selection
    // generation. Doing so can cancel an explicit linked-work navigation whose placement
    // probe is in flight while live sync refreshes the source context.
    void loadContext(opened, recordRequest.current, false, true);
  }, [contextLoading, contextRefresh, mode, opened, mutationIntents, mutationRegistry]);

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
      if (!libraryToolsOpen) {
        changeLibraryToolsOpen(true);
        requestAnimationFrame(() => searchRef.current?.focus());
        return;
      }
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, [libraryToolsOpen, view]);

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
    const showReview = new URLSearchParams(window.location.search).get("review") === "1";
    void openExactWork(activeId, target).then(() => { if (showReview) setTab("reviews"); });
  }, [activeId, opened, view]);

  useEffect(() => {
    if (view !== "library" || urlWorkRestore.current !== null) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("work") === openedId) return;
    if (openedId) url.searchParams.set("work", openedId);
    else url.searchParams.delete("work");
    window.history.replaceState(window.history.state, "", url);
  }, [openedId, view]);

  function applyProjectSelection(id: string) {
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

  function chooseProject(id: string) {
    if (
      id !== activeId
      && activeId
      && selectMutationScope(mutationRegistry.getSnapshot(), { projectId: activeId }).intents.some((intent) => !["dismiss_job_completion_report", "create_job_completion_report_follow_up", "respond_to_work_follow_up"].includes(intent.kind))
    ) {
      setNotice({
        message: "Resolve pending mutations before switching projects. Reloading would lose the exact retry request.",
        error: true
      });
      return;
    }
    ++workPlacementRequest.current;
    if (id !== activeId && opened) {
      if (!leavingOpenedWorkAllowed()) return;
      clearSelection();
    }
    applyProjectSelection(id);
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
      setProjects((items) => [...items, created].sort((a, b) =>
        a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
      ));
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
    setCreateExternalReferences([]);
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
        ...(createExternalReferences.length ? { external_references: normalizeExternalReferences(createExternalReferences) } : {}),
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

  function handleProjectSaved(saved: Project) {
    if (saved.id !== activeIdRef.current) return;
    setProjects((items) => items
      .map((item) => item.id === saved.id ? saved : item)
      .sort((left, right) =>
        left.name.localeCompare(right.name) || left.id.localeCompare(right.id)
      ));
  }

  async function loadContext(
    summary: WorkSummary,
    requestId = ++recordRequest.current,
    preserveEditDraft = false,
    recoverPlacement = false
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
      if (
        recordRequest.current === requestId
        && recoverPlacement
        && isDefinitiveWorkPlacementMiss(error)
      ) {
        setContext(null);
        setContextReconciliationRequired(true);
        const recovery = await recoverCurrentWorkPlacement(
          summary,
          requestId,
          "context_refresh",
          preserveEditDraft
        );
        return recovery === "relocated"
          ? "loaded"
          : recovery === "superseded" ? "superseded" : "failed";
      }
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
    setJobReportDraft(emptyJobReportDraft());
    setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
    setCompletionEvidenceIssues([]);
    setCheckpointKind("progress");
    setCheckpointActionError("");
    setEventRefresh((value) => value + 1);
    void loadContext(summary, requestId, false, true);
  }

  async function openExactWork(
    projectId: string,
    workItemId: string,
    moveRecovery?: WorkPlacementSummary
  ): Promise<boolean> {
    const requestId = ++recordRequest.current;
    exactContextTarget.current = { projectId, workItemId, moveRecovery };
    setContext(null);
    setContextReconciliationRequired(true);
    setContextLoading(true);
    setContextError("");
    setCheckpointOffset(0);
    setCheckpointPage(null);
    // A post-Move retry must retain every draft until a verified context is loaded.
    // Fresh exact opens still clear eagerly so an unrelated record never inherits them.
    if (!moveRecovery) {
      setMode("view");
      setMergeOpen(false);
      setEditError("");
      setConflict(null);
      setCheckpointBody("");
      setCheckpointBranch("");
      setCheckpointCommit("");
      setCheckpointAffectedPaths("");
      setCheckpointAffectedPathsError("");
      setCheckpointTags("");
      setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
      setCompletionEvidenceIssues([]);
      setCheckpointActionError("");
    }
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
      if (moveRecovery) {
        // The Move guard already confirmed that these drafts may be discarded. Delay
        // that discard until the exact target context has actually been verified.
        setMode("view");
        setMergeOpen(false);
        setEditError("");
        setConflict(null);
        setCheckpointBody("");
        setCheckpointBranch("");
        setCheckpointCommit("");
        setCheckpointAffectedPaths("");
        setCheckpointAffectedPathsError("");
        setCheckpointTags("");
        setJobReportDraft(emptyJobReportDraft());
        setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
        setCompletionEvidenceIssues([]);
        setCheckpointActionError("");
      }
      setContextReconciliationRequired(false);
      exactContextTarget.current = null;
      lastLoadedContextRequest.current = requestId;
      setEventRefresh((value) => value + 1);
      return true;
    } catch (cause) {
      if (
        recordRequest.current === requestId
        && moveRecovery
        && isDefinitiveWorkPlacementMiss(cause)
      ) {
        exactContextTarget.current = null;
        const recovery = await recoverCurrentWorkPlacement(
          moveRecovery,
          requestId,
          "move_action",
          true
        );
        // An ambiguous scan is retryable. Keep the exact moved-to project as the
        // first probe, then scan the complete catalog again if it still returns 404.
        if (recordRequest.current === requestId && recovery === "ambiguous") {
          exactContextTarget.current = { projectId, workItemId, moveRecovery };
        }
        return recovery === "relocated";
      }
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

  async function fetchFreshProjectCatalog(
    isCurrent: () => boolean,
    signal?: AbortSignal
  ): Promise<Project[] | null> {
    return loadCompleteProjectCatalog(
      (offset) => api<unknown>(
        `/projects?limit=100&offset=${offset}`,
        signal ? { signal } : undefined
      ),
      isCurrent
    );
  }

  async function scanWorkContexts(
    catalog: readonly Project[],
    workItemId: string,
    preferredProjectId: string,
    isCurrent: () => boolean
  ): Promise<WorkContextScanResult> {
    if (!sameProjectCatalog(catalog, catalog)) {
      throw new Error("The work-placement lookup scope is invalid.");
    }
    const ordered = [
      ...catalog.filter((project) => sameUuid(project.id, preferredProjectId)),
      ...catalog.filter((project) => !sameUuid(project.id, preferredProjectId))
    ];
    for (const candidate of ordered) {
      if (!isCurrent()) return { kind: "superseded" };
      try {
        const value = await api<unknown>(
          `${workItemPath(candidate.id, workItemId)}/context?recent_limit=5&recent_event_limit=10`
        );
        if (!isCurrent()) return { kind: "superseded" };
        const full = decodeWorkContext(value, candidate.id, workItemId);
        if (!isCurrent()) return { kind: "superseded" };
        return { kind: "found", project: candidate, context: full };
      } catch (error) {
        if (!isCurrent()) return { kind: "superseded" };
        if (isDefinitiveWorkPlacementMiss(error)) continue;
        throw error;
      }
    }
    return isCurrent() ? { kind: "absent" } : { kind: "superseded" };
  }

  async function recoverCurrentWorkPlacement(
    staleSummary: WorkPlacementSummary,
    recordRequestId: number,
    reason: WorkPlacementRecoveryReason,
    preserveEditDraft = false
  ): Promise<WorkPlacementRecoveryResult> {
    const placementRequestId = ++workPlacementRequest.current;
    const catalogRequestId = projectCatalogRequest.current;
    const isCurrent = () =>
      recordRequest.current === recordRequestId
      && workPlacementRequest.current === placementRequestId;
    let latestCatalog: Project[] | null = null;

    function updateCatalog(catalog: readonly Project[]): void {
      if (projectCatalogRequest.current !== catalogRequestId) return;
      projectCatalogRequest.current += 1;
      setProjects([...catalog]);
      setProjectsError("");
      setProjectsLoading(false);
    }

    function commitFound(
      found: Extract<WorkContextScanResult, { kind: "found" }>,
      catalog: readonly Project[] | null
    ): WorkPlacementRecoveryResult {
      if (!isCurrent()) return "superseded";
      const targetProjectId = found.project.id;
      const sourceProjectId = staleSummary.work_item.project_id;
      const projectChanged = !sameUuid(sourceProjectId, targetProjectId);
      const displayStatus = preservedWorkMoveDisplayStatus(found.context);
      if (catalog) updateCatalog(catalog);
      if (projectChanged) {
        applyProjectSelection(targetProjectId);
        setJobReportDraft((draft) => ({ ...draft, promptRevision: null }));
      }
      setStatus(displayStatus);
      setOpened(summaryFromContext(found.context));
      setContext(found.context);
      if (!preserveEditDraft) {
        setEditDraft(draftFromWork(found.context.work_item));
        setMode("view");
        setMergeOpen(false);
        setEditError("");
        setConflict(null);
      }
      setCheckpointOffset(0);
      setCheckpointPage(null);
      setContextError("");
      setContextReconciliationRequired(false);
      setContextLoading(false);
      exactContextTarget.current = null;
      lastLoadedContextRequest.current = recordRequestId;
      setRefresh((value) => value + 1);
      setAttentionRefresh((value) => value + 1);
      setReportRefresh((value) => value + 1);
      setCheckpointRefresh((value) => value + 1);
      setEventRefresh((value) => value + 1);
      setNotice({
        message: reason === "move_action"
          ? `The work item had already moved to “${found.project.name}” before this move finished. Its current context was verified there with its ${statusFilterLabels[displayStatus]} status preserved.`
          : `The work item moved to “${found.project.name}” in another session. Its current context was verified there with its ${statusFilterLabels[displayStatus]} status preserved.`
      });
      return "relocated";
    }

    function commitAmbiguous(catalog: readonly Project[] | null): WorkPlacementRecoveryResult {
      if (!isCurrent()) return "superseded";
      if (catalog) updateCatalog(catalog);
      const message = "This work item is no longer in its previously selected project, but its current placement could not be verified. Your drafts are still here; retry the context load to keep looking.";
      setContext(null);
      setContextError(message);
      setContextReconciliationRequired(true);
      setContextLoading(false);
      exactContextTarget.current = null;
      setNotice({ message, error: true });
      return "ambiguous";
    }

    try {
      if (sameProjectCatalog(projects, projects)) {
        const initial = await scanWorkContexts(
          projects,
          staleSummary.work_item.id,
          staleSummary.work_item.project_id,
          isCurrent
        );
        if (initial.kind === "superseded") return "superseded";
        if (initial.kind === "found") return commitFound(initial, null);
      }

      const firstCatalog = await fetchFreshProjectCatalog(isCurrent);
      if (!firstCatalog) return "superseded";
      latestCatalog = firstCatalog;
      const firstScan = await scanWorkContexts(
        firstCatalog,
        staleSummary.work_item.id,
        staleSummary.work_item.project_id,
        isCurrent
      );
      if (firstScan.kind === "superseded") return "superseded";
      if (firstScan.kind === "found") return commitFound(firstScan, firstCatalog);

      const secondCatalog = await fetchFreshProjectCatalog(isCurrent);
      if (!secondCatalog) return "superseded";
      latestCatalog = secondCatalog;
      const secondScan = await scanWorkContexts(
        secondCatalog,
        staleSummary.work_item.id,
        staleSummary.work_item.project_id,
        isCurrent
      );
      if (secondScan.kind === "superseded") return "superseded";
      if (secondScan.kind === "found") return commitFound(secondScan, secondCatalog);
      if (!sameProjectCatalog(firstCatalog, secondCatalog)) {
        return commitAmbiguous(secondCatalog);
      }
      // Project-scoped probes are not an atomic locator: a concurrent move can remain
      // behind both sequential scans even when the catalog itself is unchanged.
      if (secondCatalog.length > 0) return commitAmbiguous(secondCatalog);
      if (!isCurrent()) return "superseded";
      updateCatalog(secondCatalog);
      const selectedProjectId = activeIdRef.current;
      if (
        selectedProjectId
        && !secondCatalog.some((project) => sameUuid(project.id, selectedProjectId))
      ) {
        applyProjectSelection(secondCatalog[0]?.id ?? "");
      }
      const message = "This work item is no longer available in any current project. The stale selection was cleared.";
      setNotice({ message, error: true });
      clearSelection();
      return "absent";
    } catch {
      return commitAmbiguous(latestCatalog);
    }
  }

  async function currentWorkProject(
    workItemId: string,
    preferredProjectId: string | null,
    isCurrent: () => boolean = () => true
  ): Promise<string | null> {
    return resolveCurrentWorkProject(
      projects,
      workItemId,
      preferredProjectId,
      async (projectId, candidateWorkItemId) => {
        if (!isCurrent()) return false;
        try {
          const value = await api<unknown>(workItemPath(projectId, candidateWorkItemId));
          if (!isCurrent()) return false;
          decodeWorkItemDetail(value, projectId, candidateWorkItemId);
          if (!isCurrent()) return false;
          return true;
        } catch (error) {
          if (!isCurrent()) return false;
          if (isDefinitiveWorkPlacementMiss(error)) {
            return false;
          }
          throw error;
        }
      }
    );
  }

  async function openWorkAtCurrentPlacement(
    workItemId: string,
    preferredProjectId: string | null = activeIdRef.current || null
  ): Promise<void> {
    if (mutationRegistry.hasDispatched()) {
      setNotice({
        message: "Resolve pending mutations before opening another work item.",
        error: true
      });
      return;
    }
    const requestId = ++workPlacementRequest.current;
    const recordRequestId = recordRequest.current;
    const isCurrent = () =>
      requestId === workPlacementRequest.current
      && recordRequestId === recordRequest.current;
    try {
      const projectId = await currentWorkProject(
        workItemId,
        preferredProjectId,
        isCurrent
      );
      if (!isCurrent()) return;
      if (!projectId) {
        setNotice({
          message: "This linked work item is not in any current project.",
          error: true
        });
        return;
      }
      const currentOpen = openedRef.current;
      if (
        currentOpen
        && sameUuid(currentOpen.work_item.id, workItemId)
        && sameUuid(currentOpen.work_item.project_id, projectId)
      ) return;
      if (mutationRegistry.hasDispatched()) {
        setNotice({
          message: "Resolve pending mutations before opening another work item.",
          error: true
        });
        return;
      }
      if (!leavingOpenedWorkAllowed()) return;
      if (!isCurrent()) return;
      clearSelection();
      applyProjectSelection(projectId);
      const loaded = await openExactWork(projectId, workItemId);
      if (!loaded && requestId === workPlacementRequest.current) {
        setNotice({
          message: "The linked work item’s current project was found, but its context could not be opened. Retry the link.",
          error: true
        });
      }
    } catch (error) {
      if (isCurrent()) {
        setNotice({ message: errorMessage(error), error: true });
      }
    }
  }

  async function navigateToCurrentWorkPlacement(
    workItemId: string,
    preferredProjectId: string | null
  ): Promise<void> {
    if (mutationRegistry.hasDispatched()) {
      setNotice({
        message: "Resolve pending mutations before leaving this dashboard document.",
        error: true
      });
      return;
    }
    const requestId = ++workPlacementRequest.current;
    const recordRequestId = recordRequest.current;
    const isCurrent = () =>
      requestId === workPlacementRequest.current
      && recordRequestId === recordRequest.current;
    try {
      const projectId = await currentWorkProject(
        workItemId,
        preferredProjectId,
        isCurrent
      );
      if (!isCurrent()) return;
      if (!projectId) {
        setNotice({
          message: "This linked work item is not in any current project.",
          error: true
        });
        return;
      }
      if (mutationRegistry.hasDispatched()) {
        setNotice({
          message: "Resolve pending mutations before leaving this dashboard document.",
          error: true
        });
        return;
      }
      if (!isCurrent()) return;
      try {
        localStorage.setItem(dashboardStorageKeys.project, projectId);
        if (localStorage.getItem(dashboardStorageKeys.project) !== projectId) {
          throw new Error("Project selection was not retained.");
        }
      } catch {
        setNotice({
          message: "The linked work item was found, but its project could not be selected safely.",
          error: true
        });
        return;
      }
      window.location.assign(`/?work=${encodeURIComponent(workItemId)}`);
    } catch (error) {
      if (isCurrent()) {
        setNotice({ message: errorMessage(error), error: true });
      }
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
    setJobReportDraft(emptyJobReportDraft());
    setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
    setCompletionEvidenceIssues([]);
  }

  function viewDuplicateGroup(canonicalId: string): void {
    if (!leavingOpenedWorkAllowed()) return;
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
      && selectMutationScope(mutationRegistry.getSnapshot(), { conflictKeys: [mutationWorkKey(opened.work_item.project_id, opened.work_item.id)] }).intents.some((intent) => intent.kind !== "respond_to_work_follow_up")
    ) {
      setNotice({
        message: "Resolve the pending mutation before closing this work view.",
        error: true
      });
      return false;
    }
    const checkpointEdited = Boolean(checkpointBody || checkpointBranch || checkpointCommit
      || checkpointAffectedPaths || checkpointTags
      || !completionEvidenceDraftIsEmpty(completionEvidenceDraft));
    const reportEdited = jobReportDraftHasEdits(jobReportDraft);
    const discardMessage = checkpointEdited && reportEdited
      ? "Discard your unsaved checkpoint and job completion report?"
      : reportEdited ? "Discard your unsaved job completion report?"
        : "Discard your unsaved checkpoint?";
    if ((checkpointEdited || reportEdited) && !window.confirm(discardMessage)) return false;
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

  function changeLibraryToolsOpen(open: boolean): void {
    document.documentElement.dataset.libraryTools = open ? "open" : "closed";
    setLibraryToolsOpen(open);
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
      void openExactWork(target.projectId, target.workItemId, target.moveRecovery);
    } else if (opened) {
      void loadContext(opened, ++recordRequest.current, false, true);
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
    if (!sameExternalReferences(editDraft.externalReferences, base.external_references)) {
      try { patch.external_references = normalizeExternalReferences(editDraft.externalReferences); }
      catch (error) { setEditError(errorMessage(error)); return; }
    }
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
      if (patch.status === "wont-do" || patch.status === "promoted") patch.job_completion_report = jobReportFromDraft(jobReportDraft);
      const saved = await mutationRegistry.execute({
        kind: "update_work",
        slot: `update-work:${base.project_id}:${base.id}`,
        projectId: base.project_id,
        conflictKeys: [mutationWorkKey(base.project_id, base.id)],
        method: "PATCH",
        path: workItemPath(base.project_id, base.id),
        payload: patch
      });
      if (saved.job_completion_report) { setJobReportDraft(emptyJobReportDraft()); setReportRefresh((value) => value + 1); }
      const { job_completion_report: _report, ...savedWork } = saved;
      const savedSummary = { ...opened, work_item: savedWork };
      setContext((value) => value ? { ...value, work_item: savedWork } : value);
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

  async function saveCheckpoint(complete: boolean, handoff?: CodeReviewHandoff) {
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
    const selection = recordRequest.current;
    try {
      if (complete) {
        const latestSettings = decodeProjectSettings(await api<unknown>(`/projects/${context.work_item.project_id}/settings`), context.work_item.project_id);
        if (recordRequest.current !== selection) return;
        handleProjectSettingsSaved(latestSettings);
        const decision = codeReviewDecision(latestSettings, context.work_item.priority, context.code_review_context?.remediation_depth ?? 0);
        if (decision === "mandatory" && !handoff) {
          setReviewCloseout({ mode: "checkpoint", summary: summaryWithContext(opened!, context), revision: latestSettings.revision });
          if (reviewDraftWorkId.current !== context.work_item.id) setReviewHandoff(emptyReviewHandoff(project?.repository_url));
          reviewDraftWorkId.current = context.work_item.id; setReviewCloseoutError("");
          return;
        }
        if (handoff && (decision !== "mandatory" || reviewCloseout?.revision !== latestSettings.revision)) {
          setReviewCloseoutError("Project review settings changed. Your handoff is kept. Close this dialog, review the refreshed settings, then prepare a new completion.");
          return;
        }
        if (jobReportDraft.promptRevision !== latestSettings.revision) {
          throw new Error("Project instructions changed. Review the current report prompt and accept its revision before completing. Your draft is kept.");
        }
      }
      const checkpoint = checkpointPayload(
        checkpointBody,
        checkpointBranch,
        checkpointCommit,
        checkpointTags,
        checkpointAffectedPaths
      );
      const base = workItemPath(context.work_item.project_id, context.work_item.id);
      if (complete) {
        const report = jobReportFromDraft(jobReportDraft);
        const completed = await mutationRegistry.execute({
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
            job_completion_report: report,
            ...(handoff ? { code_review_handoff: handoff } : {}),
            ...(completionEvidence ? { completion_evidence: completionEvidence } : {})
          }
        });
        setReviewCloseout(null);
        reviewDraftWorkId.current = null;
        if (completed.agent_follow_ups?.length || completed.code_review_request) setTab("reviews");
        setNotice({ message: "Human report and completion checkpoint recorded. Work marked done." });
        setJobReportDraft(emptyJobReportDraft());
        setReportRefresh((value) => value + 1);
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
      if (handoff) setReviewCloseoutError(errorMessage(error));
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

  async function showMovedWork(
    result: WorkMoveResult,
    displayStatus: WorkMoveDisplayStatus = result.preserved_status,
    previousSummary: WorkSummary | null = openedRef.current
  ): Promise<void> {
    const target = projects.find((item) => item.id === result.target_project_id);
    const movedSummary = summaryAfterWorkMove(previousSummary, result, displayStatus);
    const placementRequestId = ++workPlacementRequest.current;
    const requestId = ++recordRequest.current;
    const isCurrent = () =>
      workPlacementRequest.current === placementRequestId
      && recordRequest.current === requestId;
    applyProjectSelection(result.target_project_id);
    setStatus(displayStatus);
    setOpened(movedSummary);
    setContext(null);
    setContextLoading(true);
    setContextError("");
    setContextReconciliationRequired(true);
    setJobReportDraft((draft) => ({ ...draft, promptRevision: null }));
    const moveRecovery = movedSummary ?? { work_item: result.work_item };
    exactContextTarget.current = {
      projectId: result.target_project_id,
      workItemId: result.work_item.id,
      moveRecovery
    };
    setRefresh((value) => value + 1);
    setAttentionRefresh((value) => value + 1);
    let loaded: "loaded" | "failed" | "superseded" | WorkPlacementRecoveryResult;
    try {
      const value = await api<unknown>(
        `${workItemPath(result.target_project_id, result.work_item.id)}/context?recent_limit=5&recent_event_limit=10`
      );
      if (!isCurrent()) return;
      const full = decodeWorkContext(
        value,
        result.target_project_id,
        result.work_item.id
      );
      if (!isCurrent()) return;
      setStatus(preservedWorkMoveDisplayStatus(full));
      setOpened(summaryFromContext(full));
      setContext(full);
      setEditDraft(draftFromWork(full.work_item));
      setContextError("");
      setContextReconciliationRequired(false);
      exactContextTarget.current = null;
      lastLoadedContextRequest.current = requestId;
      setCheckpointRefresh((value) => value + 1);
      setEventRefresh((value) => value + 1);
      loaded = "loaded";
    } catch (error) {
      if (!isCurrent()) return;
      if (isDefinitiveWorkPlacementMiss(error)) {
        exactContextTarget.current = null;
        loaded = await recoverCurrentWorkPlacement(
          moveRecovery,
          requestId,
          "move_action",
          true
        );
        if (isCurrent() && loaded === "ambiguous") {
          exactContextTarget.current = {
            projectId: result.target_project_id,
            workItemId: result.work_item.id,
            moveRecovery
          };
        }
      } else {
        setContextError(errorMessage(error));
        loaded = "failed";
      }
    } finally {
      if (isCurrent()) setContextLoading(false);
    }
    if (!isCurrent()) return;
    if (loaded === "loaded") {
      setCheckpointBody("");
      setCheckpointBranch("");
      setCheckpointCommit("");
      setCheckpointAffectedPaths("");
      setCheckpointAffectedPathsError("");
      setCheckpointTags("");
      setJobReportDraft(emptyJobReportDraft());
      setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
      setCompletionEvidenceIssues([]);
      setCheckpointActionError("");
      setNotice({
        message: `Work item moved to “${target?.name ?? "the selected project"}” with its ${statusFilterLabels[displayStatus]} status preserved.`
      });
    } else if (loaded === "failed") {
      setNotice({
        message: "The work item was moved and the target project is selected, but its context could not be opened there. Retry or use Refresh before continuing.",
        error: true
      });
    }
  }

  async function moveWork(
    targetProjectId: string,
    requestedSummary?: WorkSummary
  ): Promise<void> {
    const selectedContext = context
      && (!requestedSummary
        || sameUuid(context.work_item.id, requestedSummary.work_item.id)
          && sameUuid(context.work_item.project_id, requestedSummary.work_item.project_id))
      ? context
      : null;
    const currentSummary = openedRef.current;
    const sourceSummaryBase = requestedSummary
      ?? (selectedContext && currentSummary
        && sameUuid(currentSummary.work_item.id, selectedContext.work_item.id)
        && sameUuid(currentSummary.work_item.project_id, selectedContext.work_item.project_id)
        ? summaryWithContext(currentSummary, selectedContext)
        : selectedContext ? summaryFromContext(selectedContext) : null);
    if (!sourceSummaryBase) return;
    const requestedWork = sourceSummaryBase.work_item;
    const selectionRequestId = recordRequest.current;
    const selectionPlacementRequestId = workPlacementRequest.current;
    const startedWithoutSelection = selectedWorkItemId() === null;
    const sourceWasOpened = Boolean(
      currentSummary
      && sameUuid(currentSummary.work_item.id, requestedWork.id)
      && sameUuid(currentSummary.work_item.project_id, requestedWork.project_id)
    );
    const sourceIsStillOpened = () => Boolean(
      sourceWasOpened
      && recordRequest.current === selectionRequestId
      && workPlacementRequest.current === selectionPlacementRequestId
      && exactContextTarget.current === null
      && openedRef.current
      && sameUuid(openedRef.current.work_item.id, requestedWork.id)
      && sameUuid(openedRef.current.work_item.project_id, requestedWork.project_id)
    );
    const canFollowMovedWork = () => sourceIsStillOpened() || Boolean(
      startedWithoutSelection
      && recordRequest.current === selectionRequestId
      && workPlacementRequest.current === selectionPlacementRequestId
      && openedRef.current === null
      && exactContextTarget.current === null
    );
    if (movingId || targetProjectId === requestedWork.project_id) return;
    if (!projects.some((item) => item.id === targetProjectId)) {
      setNotice({ message: "That target project is no longer available. Refresh and try again.", error: true });
      return;
    }
    const conflictKeys = [
      mutationWorkKey(requestedWork.project_id, requestedWork.id),
      mutationWorkKey(targetProjectId, requestedWork.id)
    ];
    if (mutationRegistry.blocks(conflictKeys)) {
      setNotice({
        message: "Resolve the pending mutation before moving this work item.",
        error: true
      });
      return;
    }
    if (sourceWasOpened && !leavingOpenedWorkAllowed()) return;
    let sourceSummary = sourceSummaryBase;
    setMovingId(requestedWork.id);
    try {
      let sourceContext = selectedContext;
      if (!sourceContext) {
        const value = await api<unknown>(
          `${workItemPath(requestedWork.project_id, requestedWork.id)}/context?recent_limit=0&recent_event_limit=0`
        );
        sourceContext = decodeWorkContext(
          value,
          requestedWork.project_id,
          requestedWork.id
        );
        sourceSummary = summaryWithContext(sourceSummary, sourceContext);
      }
      const disabledReason = workMoveDisabledReason(
        sourceContext,
        mutationRegistry.blocks(conflictKeys)
      );
      if (disabledReason) {
        setNotice({ message: disabledReason, error: true });
        return;
      }
      const source = sourceContext.work_item;
      const displayStatus = preservedWorkMoveDisplayStatus(sourceContext);
      const payload: WorkMoveInput = {
        target_project_id: targetProjectId,
        expected_version: source.version,
        actor: dashboardMutationActor(dashboardSessionId())
      };
      const result = await mutationRegistry.execute({
        kind: "move_work",
        slot: `move-work:${source.project_id}:${source.id}`,
        projectId: source.project_id,
        conflictKeys,
        method: "POST",
        path: `${workItemPath(source.project_id, source.id)}/move`,
        payload,
        expectedSourceWorkStatus: source.status
      });
      if (canFollowMovedWork()) {
        await showMovedWork(result, displayStatus, sourceSummary);
      } else {
        const target = projects.find((item) => item.id === result.target_project_id);
        setRefresh((value) => value + 1);
        setAttentionRefresh((value) => value + 1);
        setNotice({
          message: `Work item moved to “${target?.name ?? "the target project"}” with its ${statusFilterLabels[displayStatus]} status preserved.`
        });
      }
    } catch (error) {
      if (error instanceof ApiError && error.code === "work_move_review_history_conflict") {
        setNotice({ message: "This work has retained code review policy, recommendation or remediation history and must remain in its original project. Reopening does not erase that history.", error: true });
      } else if (isDefinitiveWorkPlacementMiss(error)) {
        if (canFollowMovedWork()) {
          const requestId = ++recordRequest.current;
          exactContextTarget.current = null;
          setContext(null);
          setContextLoading(true);
          setContextError("");
          setContextReconciliationRequired(true);
          await recoverCurrentWorkPlacement(
            sourceSummary,
            requestId,
            "move_action",
            sourceWasOpened
          );
        } else {
          setRefresh((value) => value + 1);
          setNotice({
            message: "This work item is no longer in its listed source project. The work queue is refreshing.",
            error: true
          });
        }
      } else if (isVersionConflict(error)) {
        if (!sourceIsStillOpened()) {
          setRefresh((value) => value + 1);
          setNotice({
            message: "This work item changed before it could be moved. The work queue is refreshing.",
            error: true
          });
        } else {
          const reconciled = await reloadOpenContext();
          setNotice({
            message: reconciled
              ? "This work item changed before it could be moved. Its current version is ready for review."
              : "This work item changed before it could be moved, and its current state could not be reloaded. Use Refresh before continuing.",
            error: true
          });
        }
      } else {
        setNotice({ message: errorMessage(error), error: true });
      }
    } finally {
      setMovingId(null);
    }
  }

  async function changeManualStatus(
    action: ManualStatusAction,
    summary: WorkSummary,
    handoff?: CodeReviewHandoff
  ): Promise<void> {
    const work = summary.work_item;
    const actionRecordRequestId = recordRequest.current;
    const actionPlacementRequestId = workPlacementRequest.current;
    const actionProjectId = activeIdRef.current;
    const actionNavigationIsCurrent = () =>
      recordRequest.current === actionRecordRequestId
      && workPlacementRequest.current === actionPlacementRequestId
      && activeIdRef.current === actionProjectId
      && exactContextTarget.current === null;
    const actionSourceIsStillOpened = () => {
      const current = openedRef.current;
      return Boolean(actionNavigationIsCurrent() && current
        && sameUuid(current.work_item.id, work.id)
        && sameUuid(current.work_item.project_id, work.project_id));
    };
    let settings = projectSettings?.project_id === work.project_id
      ? projectSettings
      : null;
    if (statusChangingId || summary.readiness.is_duplicate) return;
    if (action === "defer" && work.status === "deferred") return;
    if (action !== "defer") {
      const reason = statusActionDisabledReason(action, summary.readiness, Boolean(settings));
      if (reason) {
        setNotice({ message: reason, error: true });
        return;
      }
    }

    const conflictKeys = [mutationWorkKey(work.project_id, work.id)];
    if (mutationRegistry.blocks(conflictKeys)) {
      setNotice({
        message: "Resolve the pending mutation for this work item before changing its status.",
        error: true
      });
      return;
    }

    const basePath = workItemPath(work.project_id, work.id);
    const actor = dashboardMutationActor(dashboardSessionId());
    let currentWork = work;
    setStatusChangingId(work.id);
    try {
      const latestValue = await api<unknown>(`${basePath}/context?recent_limit=0&recent_event_limit=0`);
      const latestContext = decodeWorkContext(latestValue, work.project_id, work.id);
      if (latestContext.code_review_context?.current_review || latestContext.code_review_context?.pending_follow_up) {
        throw new Error("Use Reopen work to explicitly supersede the outstanding review or recommendation before changing status.");
      }
      if (latestContext.work_item.version !== work.version) throw new Error("This work item changed. Refresh it before making a status decision.");
      if (action === "done") {
        settings = decodeProjectSettings(await api<unknown>(`/projects/${work.project_id}/settings`), work.project_id);
        handleProjectSettingsSaved(settings);
        const decision = codeReviewDecision(settings, work.priority, latestContext.code_review_context?.remediation_depth ?? 0);
        if (decision === "mandatory" && !handoff) {
          setReviewCloseout({ mode: "manual", summary: summaryWithContext(summary, latestContext), revision: settings.revision });
          if (reviewDraftWorkId.current !== work.id) setReviewHandoff(emptyReviewHandoff(projects.find((item) => item.id === work.project_id)?.repository_url));
          reviewDraftWorkId.current = work.id; setReviewCloseoutError("");
          return;
        }
        if (handoff && (decision !== "mandatory" || reviewCloseout?.revision !== settings.revision)) {
          setReviewCloseoutError("Project review settings changed. Your handoff is kept. Close this dialog and review the refreshed policy before preparing another completion.");
          return;
        }
      }
      if (
        action !== "active"
        && (summary.readiness.has_active_lease || summary.readiness.has_dropped_lease)
      ) {
        const payload: DashboardWorkPendingInput = {
          expected_version: currentWork.version,
          expected_lease_state: summary.readiness.has_active_lease ? "active" : "dropped",
          expected_active_lease: summary.readiness.has_active_lease
            ? summary.readiness.active_lease
            : null,
          actor
        };
        const value = await api<unknown>(`${basePath}/return-to-pending`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        decodeLeaseReleaseResult(value, work.id);
      }

      if (currentWork.status !== "pending") {
        const reopened = await mutationRegistry.execute({
          kind: "update_work",
          slot: `update-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "PATCH",
          path: basePath,
          payload: {
            expected_version: currentWork.version,
            status: "pending",
            actor
          }
        });
        const { job_completion_report: _report, ...reopenedWork } = reopened;
        currentWork = reopenedWork;
      }

      if (action === "active") {
        const payload: DashboardWorkActivationInput = {
          expected_version: currentWork.version,
          actor,
          claim_request_id: crypto.randomUUID()
        };
        const value = await api<unknown>(`${basePath}/activate`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        decodeDashboardActivationResult(value, actor);
      } else if (action === "defer") {
        await mutationRegistry.execute({
          kind: "defer_work",
          slot: `defer-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "POST",
          path: `${basePath}/defer`,
          payload: {
            expected_version: currentWork.version,
            actor
          }
        });
      } else if (action === "done") {
        if (!settings) throw new Error("Project report settings are not ready.");
        const checkpoint = {
          ...checkpointPayload(humanDecisionCompletionCheckpoint(currentWork)),
          source_metadata: {
            decision: "explicit-human",
            action: "manual-status-change"
          }
        };
        const completed = await mutationRegistry.execute({
          kind: "complete_work",
          slot: `complete-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "POST",
          path: `${basePath}/complete`,
          payload: {
            expected_version: currentWork.version,
            checkpoint,
            ...(handoff ? { code_review_handoff: handoff } : {}),
            job_completion_report: humanDecisionReport(
              currentWork,
              "done",
              settings.revision
            )
          }
        });
        setReviewCloseout(null);
        if (completed.agent_follow_ups?.length || completed.code_review_request) {
          const currentOpen = openedRef.current;
          if (actionNavigationIsCurrent() && (
            actionSourceIsStillOpened()
            || currentOpen === null
              && sameUuid(activeIdRef.current, work.project_id)
          )) {
            // A successful terminal action owns these drafts only while its
            // source is still selected. Clear them before openExactWork changes
            // the request generation, including when that reload later fails.
            if (actionSourceIsStillOpened()) {
              setJobReportDraft(emptyJobReportDraft());
              setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
              setCompletionEvidenceIssues([]);
            }
            const openedReview = await openExactWork(work.project_id, work.id);
            if (openedReview) setTab("reviews");
          }
        }
        if (actionSourceIsStillOpened()) setCheckpointOffset(0);
        setCheckpointRefresh((value) => value + 1);
        setReportRefresh((value) => value + 1);
      } else if (action === "wont-do" || action === "promoted") {
        if (!settings) throw new Error("Project report settings are not ready.");
        await mutationRegistry.execute({
          kind: "update_work",
          slot: `update-work:${work.project_id}:${work.id}`,
          projectId: work.project_id,
          conflictKeys,
          method: "PATCH",
          path: basePath,
          payload: {
            expected_version: currentWork.version,
            status: action,
            actor,
            job_completion_report: humanDecisionReport(
              currentWork,
              action,
              settings.revision
            )
          }
        });
        setReportRefresh((value) => value + 1);
      }

      const decision = action === "defer"
        ? "Deferred and held out of the work queue"
        : action === "pending"
          ? summary.readiness.is_gated
            ? "Pending but still needs human attention, so it remains out of ready discovery"
            : summary.readiness.is_blocked
              ? "Pending but still blocked, so it remains out of ready discovery"
              : "Pending and available in the work queue"
          : action === "active"
            ? "Active"
            : action === "done"
              ? "Done"
              : action === "wont-do"
                ? "Won’t Do"
                : "Promoted";
      setNotice({
        message: `Explicit human decision recorded: “${work.title}” is ${decision}.`
      });
      const sourceRemainsOpen = actionSourceIsStillOpened();
      if (sourceRemainsOpen && (action === "done" || action === "wont-do" || action === "promoted")) {
        setJobReportDraft(emptyJobReportDraft());
      }
      if (sourceRemainsOpen && action === "done") {
        setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
        setCompletionEvidenceIssues([]);
      }
      setEventRefresh((value) => value + 1);
      setAttentionRefresh((value) => value + 1);
      setRefresh((value) => value + 1);
      if (sourceRemainsOpen) void reloadOpenContext();
    } catch (error) {
      if (isVersionConflict(error)) setRefresh((value) => value + 1);
      if (handoff) setReviewCloseoutError(errorMessage(error));
      setNotice({ message: errorMessage(error), error: true });
    } finally {
      setStatusChangingId(null);
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
      if (intent.kind === "move_work") {
        const selectedContext = context;
        const displayStatus = selectedContext
          && intent.conflictKeys.includes(mutationWorkKey(
            selectedContext.work_item.project_id,
            selectedContext.work_item.id
          ))
          ? preservedWorkMoveDisplayStatus(selectedContext)
          : undefined;
        const result = await mutationRegistry.retry<"move_work">(intent.slot);
        await showMovedWork(result, displayStatus);
        return;
      } else if (intent.kind === "merge_work") {
        const result = await mutationRegistry.retry<"merge_work">(intent.slot);
        const selected = openedRef.current?.work_item;
        if (selected?.id === result.merge.source_work_item_id
          && selected.project_id === result.merge.project_id) {
          await merged(result);
          return;
        }
      } else {
        const result = await mutationRegistry.retry(intent.slot);
        if (intent.kind === "complete_work" && result && typeof result === "object" && ("code_review_request" in result || "agent_follow_ups" in result)) setTab("reviews");
      }
      setRefresh((value) => value + 1);
      setAttentionRefresh((value) => value + 1);
      setReportRefresh((value) => value + 1);
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
          setReviewCloseout(null);
          reviewDraftWorkId.current = null;
          setJobReportDraft(emptyJobReportDraft());
          setCompletionEvidenceDraft(emptyCompletionEvidenceDraft());
          setCompletionEvidenceIssues([]);
        }
      }
      if (intent.kind === "update_work") { setMode("view"); setReopenReview(null); }
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

  async function copyRecallPointer(summary: WorkSummary) {
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
    const requestGeneration = recordRequest.current;
    try {
      const value = await api<unknown>(`${workItemPath(projectId, summary.work_item.id)}/context?recent_limit=0&recent_event_limit=0`);
      const latest = decodeWorkContext(value, projectId, summary.work_item.id);
      if (recordRequest.current !== requestGeneration) return;
      const review = latest.code_review_context?.current_review;
      await copyText(
      workRecallPointer(summaryWithContext(summary, latest), {
        template: projectSettings.recall_pointer_template ?? undefined,
        project: pointerProject
      }) + (review ? `\n\n${warmReviewDirective(review)}` : ""),
      `${summary.work_item.id}:pointer`,
      "Recall pointer copied. Paste it into a session with Mnemonic connected."
    );
    } catch (error) { setNotice({ message: errorMessage(error), error: true }); }
  }

  async function copyColdReview() {
    const review = context?.code_review_context?.current_review;
    if (!review) return;
    const requestGeneration = recordRequest.current;
    try {
      const value = await api<unknown>(`${workItemPath(review.project_id, review.work_item_id)}/code-reviews/${review.id}`);
      const detail = decodeCodeReviewDetail(value, review.project_id, review.work_item_id, review.id);
      if (requestGeneration !== recordRequest.current) return;
      if (detail.review.state !== "requested" || detail.review.version !== review.version || detail.review.scope_sha256 !== review.scope_sha256) {
        setContextRefresh((value) => value + 1); throw new Error("The review changed. Refresh the work item before copying its prompt.");
      }
      const prompt = coldReviewPrompt({ project_id: review.project_id, work_item_id: review.work_item_id, code_review_id: review.id,
        review_version: review.version, scope_sha256: review.scope_sha256, scope: detail.scope });
      await copyText(prompt, `${review.work_item_id}:cold-review`, "Cold review prompt copied with pinned Git scope and no handoff context.");
    } catch (error) { setNotice({ message: errorMessage(error), error: true }); }
  }

  async function supersedeReview() {
    if (!reopenReview) return;
    const work = reopenReview.work_item, review = reopenReview.code_review_context?.current_review, question = reopenReview.code_review_context?.pending_follow_up;
    if (!review && !question) return;
    setReviewReopening(true); setReviewReopenError("");
    try {
      await mutationRegistry.execute({ kind: "update_work", slot: `update-work:${work.project_id}:${work.id}`, projectId: work.project_id,
        conflictKeys: [mutationWorkKey(work.project_id, work.id)], method: "PATCH", path: workItemPath(work.project_id, work.id),
        payload: { expected_version: work.version, status: "pending", actor: dashboardMutationActor(dashboardSessionId()),
          ...(review ? { supersede_code_review_id: review.id, expected_code_review_version: review.version }
            : { supersede_follow_up_id: question!.id, expected_follow_up_version: question!.version }) } });
      setReopenReview(null); setRefresh((value) => value + 1); setEventRefresh((value) => value + 1); await reloadOpenContext();
    } catch (error) { setReviewReopenError(errorMessage(error)); } finally { setReviewReopening(false); }
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
    if (!mutationRegistry.hasDispatched()) {
      if (opened && !leavingOpenedWorkAllowed()) event.preventDefault();
      return;
    }
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
  const detailNavigationBlocked = Boolean(openedWorkKey && selectMutationScope(mutationIntents, { conflictKeys: [openedWorkKey] }).intents.some((intent) => intent.kind !== "respond_to_work_follow_up"))
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
  useFailedReadRetry({
    scope: `checkpoints:${opened?.work_item.project_id}:${openedId}:${checkpointOffset}`,
    failed: Boolean(checkpointLoadError), busy: checkpointLoading, enabled: Boolean(opened),
    retry: () => setCheckpointRefresh((value) => value + 1)
  });
  useFailedReadRetry({
    scope: `context:${opened?.work_item.project_id}:${openedId}`,
    failed: Boolean(contextError), busy: contextLoading,
    enabled: Boolean(opened) && mode !== "edit" && !openedWorkMutationBlocked,
    retry: () => retryOpenedContext()
  });
  useFailedReadRetry({
    scope: `settings:${activeId}`, failed: Boolean(settingsLoadError), busy: settingsFetching,
    enabled: Boolean(activeId) && activityReadyProjectId === activeId,
    retry: () => setSettingsRefresh((value) => value + 1)
  });
  useFailedReadRetry({
    scope: "projects", failed: Boolean(projectsError), busy: projectsLoading,
    retry: () => setProjectsRefresh((value) => value + 1)
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
    <a className="skip-link" href="#main-content">{view === "settings" ? "Skip to project settings" : view === "attention" ? "Skip to human questions" : view === "summaries" ? "Skip to summaries" : "Skip to work items"}</a>
    <aside className="sidebar">
      <a href="/" className="brand" aria-label="Mnemonic home" aria-disabled={activeProjectMutationBlocked || undefined} onClick={blockNavigationWhilePending}><Logo /><span>mnemonic<span className="brand-period">.</span></span></a>
      <div className="workspace-picker">
        <label className="section-label" htmlFor="project-select">YOUR WORKSPACE</label>
        <div className="select-wrap"><select id="project-select" aria-keyshortcuts="1 2 3 4 5 6 7 8 9 0" value={activeId} disabled={projectsLoading || !projects.length || selectMutationScope(mutationIntents, { projectId: activeId }).intents.some((intent) => !["dismiss_job_completion_report", "create_job_completion_report_follow_up", "respond_to_work_follow_up"].includes(intent.kind))} onChange={(event) => chooseProject(event.target.value)}>
          {!projects.length && <option value="">{projectsLoading ? "Loading projects…" : "Select a project"}</option>}
          {projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select><span className="select-chevron" aria-hidden="true">⌄</span></div>
        <button className="new-project-button" type="button" disabled={projectsLoading || activeProjectMutationBlocked} onClick={() => { setNewProjectError(""); setProjectDialog(true); }}><Icon name="plus" size={15} />New project</button>
        {project && <button className="copy-project-button" type="button" title={`Project ID: ${project.id}`} onClick={() => void copyProjectId()}><Icon name="copy" size={13} />Copy project ID for your agent</button>}
      </div>
      <nav aria-label="Workspace navigation">
        <a className={`nav-item ${view === "library" ? "active" : ""}`} href="/" aria-current={view === "library" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="library" /><span>Work library</span><Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "summaries" ? "active" : ""}`} href="/summaries" aria-current={view === "summaries" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="box" /><span>Summaries</span>{reportCount !== null && reportCount !== "0" && <span className="summary-nav-count" aria-label={`${reportCount} undismissed summaries`}>{reportCount}</span>}<Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "attention" ? "active" : ""}`} href="/attention" aria-current={view === "attention" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="attention" /><span>Needs Attention</span>{attentionCount !== null && attentionCount > 0 && <span className="attention-nav-count" aria-label={`${attentionCount} unresolved human question${attentionCount === 1 ? "" : "s"}`}>{attentionCount}</span>}<Icon name="arrow" size={15} /></a>
        <a className={`nav-item ${view === "settings" ? "active" : ""}`} href="/settings" aria-current={view === "settings" ? "page" : undefined} onClick={blockNavigationWhilePending}><Icon name="settings" /><span>Project settings</span><Icon name="arrow" size={15} /></a>
      </nav>
      <div className="sidebar-note"><img className="note-art" src="/img/robot.svg" alt="" width={115} height={115} aria-hidden="true" /><h2>Keep your agents on the same page.</h2><p>Work units are reserved and nothing is forgotten.</p></div>
      <div className="sidebar-footer"><span className="local-dot" /><span>Local workspace</span><ThemeSelector /></div>
    </aside>

    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="breadcrumb-slash">/</span><span>{project?.name || "Getting started"}</span>{view !== "library" && <><span className="breadcrumb-slash">/</span><span>{view === "settings" ? "Project settings" : view === "summaries" ? "Summaries" : "Needs Attention"}</span></>}</div><span className="topbar-note"><span className="small-mark">m.</span>Context worth keeping</span></header>
      <div className={`page-content ${view === "library" ? "page-content-library" : ""}`}>
        {activity.error && <div className="error-notice" role="alert"><p>Activity updates: {activity.error}</p><button type="button" className="button button-secondary" onClick={activity.streamChanged ? activity.reloadSnapshot : activity.poll}>{activity.streamChanged ? "Reload current snapshot" : "Retry updates"}</button></div>}
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
              onProjectSaved={handleProjectSaved}
              onNotice={(message, error) => setNotice({ message, error })}
            />}
        </> : view === "summaries" ? <>
          <DashboardViewChrome eyebrow="WORK RESULTS FOR PEOPLE" title="Summaries"
            description={project ? `Closeout reports to review in “${project.name}”.` : "Choose a project to read its closeout reports."}
            liveSyncStatus={liveSyncStatus} onRefresh={() => { setReportRefresh((value) => value + 1); activity.poll(); }} />
          {project && activityReadyProjectId === project.id
            ? <JobReportList key={project.id} projectId={project.id} refreshSignal={reportRefresh}
                onChanged={() => { setReportRefresh((value) => value + 1); setRefresh((value) => value + 1); }}
                onOpenWork={(workItemId, preferredProjectId) => {
                  void navigateToCurrentWorkPlacement(workItemId, preferredProjectId ?? null);
                }} />
            : <div className="loading-state" role="status">{project ? "Opening the project feed…" : "Select a project to read summaries."}</div>}
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
              project={activityReadyProjectId === activeId ? project : undefined}
              refreshSignal={attentionRefresh}
              onOpen={openAttentionWork}
              onResolved={() => {
                setRefresh((value) => value + 1);
                setContextRefresh((value) => value + 1);
                setEventRefresh((value) => value + 1);
              }}
            />}
          {project && activityReadyProjectId === project.id && <CodeReviewInbox key={`attention-reviews:${project.id}`} projectId={project.id} refreshSignal={attentionRefresh} onOpen={(workItemId) => {
            if (mutationRegistry.hasDispatched()) { setNotice({ message: "Resolve pending mutations before leaving this dashboard.", error: true }); return; }
            window.location.assign(`/?work=${encodeURIComponent(workItemId)}&review=1`);
          }} />}
        </> : <>
          <DashboardViewChrome
            title="Work library"
            subject={project?.name}
            subjectDescription={project?.description || "One objective. Many immutable checkpoints. Ready for whoever continues it."}
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
                supplementaryContent={project && activityReadyProjectId === project.id
                  ? <details className="review-inbox-disclosure"><summary>Code review queue and unanswered recommendations</summary><CodeReviewInbox key={`library-reviews:${project.id}`} projectId={project.id} refreshSignal={eventRefresh + refresh} onOpen={(workItemId) => { void openExactWork(project.id, workItemId).then(() => setTab("reviews")); }} /></details>
                  : undefined}
                libraryToolsOpen={libraryToolsOpen}
                onLibraryToolsOpen={changeLibraryToolsOpen}
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
                projects={projects}
                statusChangingId={statusChangingId}
                movingId={movingId}
                reportSettingsProjectId={projectSettings?.project_id ?? null}
                isMutationBlocked={(item) => mutationRegistry.blocks([
                  mutationWorkKey(item.work_item.project_id, item.work_item.id)
                ])}
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
                onStatusAction={(action, item) => void changeManualStatus(action, item)}
                onMove={(item, targetProjectId) => void moveWork(targetProjectId, item)}
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
                  onStatusAction={(action, item) => void changeManualStatus(action, item)}
                  statusChanging={Boolean(openedId && statusChangingId === openedId)}
                  reportSettingsReady={projectSettings?.project_id === opened?.work_item.project_id}
                  allowRemediationReviews={projectSettings?.project_id === opened?.work_item.project_id ? projectSettings?.allow_remediation_code_reviews : undefined}
                  onDelete={() => { if (context) openDeletion(context.work_item); }}
                  onMove={(targetProjectId) => void moveWork(targetProjectId)}
                  projects={projects}
                  moving={Boolean(openedId && movingId === openedId)}
                  onOpenCanonical={(workItemId, preferredProjectId) => {
                    void openWorkAtCurrentPlacement(
                      workItemId,
                      preferredProjectId ?? opened?.work_item.project_id ?? null
                    );
                  }}
                  onViewDuplicateGroup={viewDuplicateGroup}
                  onCopy={(value, key, success) => void copyText(value, key, success)}
                  onCopyPointer={(item) => void copyRecallPointer(item)}
                  onCopyColdReview={() => void copyColdReview()}
                  onReopenReview={() => { if (context) { setReopenReview(context); setReviewReopenError(""); } }}
                  onReviewChanged={async () => { setEventRefresh((value) => value + 1); setAttentionRefresh((value) => value + 1); setRefresh((value) => value + 1); await reloadOpenContext(); }}
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
                  jobReportDraft={jobReportDraft}
                  onJobReportDraft={setJobReportDraft}
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
                  backDisabled={editSaving || checkpointSaving || detailNavigationBlocked}
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

    {reviewCloseout && <Dialog title="Complete work with mandatory review" wide
      busy={checkpointSaving || Boolean(statusChangingId) || mutationRegistry.blocks([mutationWorkKey(reviewCloseout.summary.work_item.project_id, reviewCloseout.summary.work_item.id)])}
      onClose={() => { if (!checkpointSaving && !statusChangingId && !mutationRegistry.blocks([mutationWorkKey(reviewCloseout.summary.work_item.project_id, reviewCloseout.summary.work_item.id)])) setReviewCloseout(null); }}
      recovery={modalRecovery(selectMutationScope(mutationIntents, { conflictKeys: [mutationWorkKey(reviewCloseout.summary.work_item.project_id, reviewCloseout.summary.work_item.id)] }).intents)}>
      <p className="dialog-intro">“{reviewCloseout.summary.work_item.title}” requires a review under project policy. Complete the implementation and attach its immutable Git scope and originating-session handoff in one operation.</p>
      <CodeReviewHandoffEditor value={reviewHandoff} onChange={setReviewHandoff} disabled={checkpointSaving || Boolean(statusChangingId) || detailMutationBlocked} />
      {reviewCloseout.mode === "checkpoint" && <JobReportEditor projectId={reviewCloseout.summary.work_item.project_id} draft={jobReportDraft} onChange={setJobReportDraft} disabled={checkpointSaving || detailMutationBlocked} />}
      {reviewCloseoutError && <p className="error-notice" role="alert">{reviewCloseoutError}</p>}
      <div className="dialog-actions"><button className="button button-secondary" disabled={checkpointSaving || Boolean(statusChangingId) || detailMutationBlocked} onClick={() => setReviewCloseout(null)}>Keep draft</button><button className="button button-primary" disabled={checkpointSaving || Boolean(statusChangingId) || detailMutationBlocked || !validReviewHandoff(reviewHandoff)} onClick={() => { void (reviewCloseout.mode === "checkpoint" ? saveCheckpoint(true, reviewHandoff) : changeManualStatus("done", reviewCloseout.summary, reviewHandoff)); }}>Complete and request review</button></div>
    </Dialog>}
    {reopenReview && <Dialog title="Reopen work and supersede its review?" busy={reviewReopening || detailMutationBlocked} onClose={() => { if (!reviewReopening && !detailMutationBlocked) setReopenReview(null); }} recovery={modalRecovery(openedPaneMutationIntents)}>
      <p>Reopen “{reopenReview.work_item.title}” as Pending. Its outstanding {reopenReview.code_review_context?.current_review ? "review request" : "recommendation question"} will be superseded. Any review lease will be invalidated. Existing notes and results remain in history.</p>
      {reopenReview.readiness.active_lease && <p>Active reviewer: {reopenReview.readiness.active_lease.holder_client}. Reopening abandons this attempt.</p>}
      {reviewReopenError && <p className="error-notice" role="alert">{reviewReopenError}</p>}
      <div className="dialog-actions"><button className="button button-secondary" disabled={reviewReopening || detailMutationBlocked} onClick={() => setReopenReview(null)}>Keep review</button><button className="button button-primary" disabled={reviewReopening || detailMutationBlocked} onClick={() => void supersedeReview()}>{reviewReopening ? "Reopening…" : "Reopen and supersede"}</button></div>
    </Dialog>}
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
      <ExternalReferencesEditor value={createExternalReferences} onChange={setCreateExternalReferences} disabled={createWorkMutationBlocked} />
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
