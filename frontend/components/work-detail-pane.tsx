"use client";

import ExternalReferences from "@/components/external-references";
import CodeReviewPanel from "@/components/code-review-panel";
import WorkReportProvenance from "@/components/work-report-provenance";
import JobReportEditor from "@/components/job-report-editor";
import type { JobReportDraft } from "@/lib/job-completion-reports";

import { createPortal } from "react-dom";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject
} from "react";
import AffectedPathsEditor from "@/components/affected-paths-editor";
import CheckpointRepositoryDeclaration from "@/components/checkpoint-repository-declaration";
import CheckpointTimeline from "@/components/checkpoint-timeline";
import CompletionEvidencePanel, {
  CompletionEvidenceEditor
} from "@/components/completion-evidence-panel";
import HumanGatePanel from "@/components/human-gate-panel";
import RelationshipPanel from "@/components/relationship-panel";
import WorkEventTimeline from "@/components/work-event-timeline";
import {
  ActiveLeaseSummary,
  OperationalBadge,
  StatusBadge,
  clientLabel,
  formatDateTime
} from "@/components/work-item-card";
import WorkItemEditor, { type WorkEditDraft } from "@/components/work-item-editor";
import WorkMergePanel from "@/components/work-merge-panel";
import { currentContext } from "@/lib/current-context";
import type {
  Checkpoint,
  CheckpointKind,
  Page,
  Project,
  WorkContext,
  WorkItem,
  WorkMergeResult,
  WorkSummary
} from "@/lib/types";
import { copyKey, detailTabs, type DetailTab } from "@/lib/work-detail-tabs";
import {
  migrationWarning,
  terminalActionDisabled,
  terminalActionGateExplanation
} from "@/lib/work-item-view";
import type {
  CompletionEvidenceDraft,
  CompletionEvidenceIssue
} from "@/lib/completion-evidence";
import {
  availableStatusActions,
  statusActionDisabledReason,
  type ManualStatusAction
} from "@/lib/work-status-actions";
import { workMoveDisabledReason } from "@/lib/work-move";

const iconPaths = {
  copy: "M9 5V3h12v14h-3M3 7h12v14H3V7Z",
  check: "m5 12 4 4L19 6",
  close: "m6 6 12 12M6 18 18 6",
  back: "M19 12H5m5-5-5 5 5 5",
  library: "M3 3h6v18H3V3Zm10 0h4l4 17-4 1-4-18Z"
};

function Icon({ name, size = 18 }: { name: keyof typeof iconPaths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={iconPaths[name]} /></svg>;
}

// The slide-in starts offset and transparent with no transition, then eases in
// on the next painted frame. The reduced-motion rule in globals.css collapses
// the durations with !important, so these inline values never override it.
const SLIDE_START: CSSProperties = {
  opacity: 0,
  transform: "translateX(28px)",
  transition: "none",
  willChange: "opacity, transform"
};

const SLIDE_END: CSSProperties = {
  opacity: 1,
  transform: "none",
  transition: "opacity 360ms cubic-bezier(.25,1,.5,1), transform 420ms cubic-bezier(.25,1,.5,1)",
  willChange: "opacity, transform"
};

export type WorkDetailPaneProps = {
  // The pane a lifecycle filter cross-dissolves when it retires the open record;
  // usePaneCrossfade owns it.
  paneRef: RefObject<HTMLElement | null>;
  opened: WorkSummary | null;
  context: WorkContext | null;
  contextLoading: boolean;
  contextError: string;
  reconciliationRequired: boolean;
  onRetryContext: () => void;
  tab: DetailTab;
  onTab: (tab: DetailTab) => void;
  mode: "view" | "edit";
  editDraft: WorkEditDraft | null;
  editSaving: boolean;
  mutationBlocked: boolean;
  editError: string;
  conflict: WorkItem | null;
  setEditDraft: (updater: (draft: WorkEditDraft) => WorkEditDraft) => void;
  onSaveEdits: (event: FormEvent<HTMLFormElement>) => void;
  onCancelEdit: () => void;
  onLoadCurrent: () => void;
  onUseCurrentVersion: () => void;
  onEdit: () => void;
  mergeOpen: boolean;
  mergeRecoveryVisible: boolean;
  onOpenMerge: () => void;
  onCloseMerge: () => void;
  onMerged: (result: WorkMergeResult) => void | Promise<void>;
  onMergeSourceChanged: () => void | Promise<void>;
  onDelete: () => void;
  onMove: (targetProjectId: string) => void;
  projects: readonly Project[];
  moving: boolean;
  onStatusAction: (action: ManualStatusAction, summary: WorkSummary) => void;
  statusChanging: boolean;
  reportSettingsReady: boolean;
  onOpenCanonical: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
  allowRemediationReviews?: boolean;
  onViewDuplicateGroup: (canonicalWorkItemId: string) => void;
  onCopy: (value: string, key: string, success: string) => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onCopyColdReview: () => void;
  onReopenReview: () => void;
  onReviewChanged: () => Promise<void>;
  copiedKey: string | null;
  checkpointPage: Page<Checkpoint> | null;
  checkpointOffset: number;
  checkpointLoading: boolean;
  checkpointLoadError: string;
  checkpointActionError: string;
  checkpointKind: Exclude<CheckpointKind, "completion">;
  checkpointBody: string;
  checkpointBranch: string;
  checkpointCommit: string;
  checkpointAffectedPaths: string;
  checkpointAffectedPathsError: string;
  checkpointTags: string;
  checkpointSaving: boolean;
  jobReportDraft: JobReportDraft;
  onJobReportDraft: (draft: JobReportDraft) => void;
  completionEvidenceDraft: CompletionEvidenceDraft;
  completionEvidenceIssues: readonly CompletionEvidenceIssue[];
  evidenceRefreshSignal: number;
  onCheckpointKind: (kind: Exclude<CheckpointKind, "completion">) => void;
  onCheckpointBody: (value: string) => void;
  onCheckpointBranch: (value: string) => void;
  onCheckpointCommit: (value: string) => void;
  onCheckpointAffectedPaths: (value: string) => void;
  onCheckpointTags: (value: string) => void;
  onCompletionEvidenceDraft: (draft: CompletionEvidenceDraft) => void;
  onAppend: () => void;
  onComplete: () => void;
  onRelationshipsChanged: () => Promise<boolean>;
  onCheckpointOffset: (offset: number) => void;
  onReloadCheckpoints: () => void;
  onEventAppended: () => Promise<boolean>;
  onGateResolved: () => Promise<void>;
  eventRefreshSignal: number;
  recovery?: ReactNode;
  notice?: ReactNode;
  onBack: () => void;
  backDisabled: boolean;
};

function ContextRetry({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="error-notice" role="alert"><p>{message}</p><button type="button" className="button button-secondary" onClick={onRetry}>Try again</button></div>;
}

function EmptyPane() {
  return <div className="detail-empty">
    <div className="empty-art"><Icon name="library" size={34} /><span /></div>
    <span className="eyebrow">WORK CONTEXT</span>
    <h2>Pick a work item.</h2>
    <p>Its current context, checkpoint history, work graph, human questions, and activity open here — nothing pops over the queue.</p>
    {/* Both key groups sit on one center line, in the shapes a keyboard gives them, so
        the caps read as one picture beside a single column of labels. The arrow labels
        name their own directions because the cluster puts down beside left and right. */}
    <div className="detail-empty-keys">
      <span className="key-cluster" aria-hidden="true">
        <kbd className="key-up">↑</kbd><kbd className="key-left">←</kbd><kbd className="key-down">↓</kbd><kbd className="key-right">→</kbd>
      </span>
      <span className="key-legend">
        <span className="detail-empty-hint">select work item (up/down)</span>
        <span className="detail-empty-hint">cycle states (left/right)</span>
      </span>
      <span className="key-digits"><kbd>1</kbd>–<kbd>0</kbd></span>
      <span className="detail-empty-hint">select a project</span>
    </div>
  </div>;
}

function DuplicateAuditPanel({
  context,
  copiedKey,
  onCopy,
  onOpenCanonical,
  onViewDuplicateGroup
}: Pick<WorkDetailPaneProps, "copiedKey" | "onCopy" | "onOpenCanonical" | "onViewDuplicateGroup"> & { context: WorkContext }) {
  const auditKey = copyKey(context.work_item.id, "audit-id");
  const mergeEvent = context.recent_events.find((event) => (
    event.event_type === "work_merged"
    && "role" in event.metadata
    && event.metadata.role === "source"
  ));
  return <section className="duplicate-audit-panel" aria-labelledby="duplicate-audit-title">
    <div>
      <span className="section-label">PERMANENT DUPLICATE AUDIT</span>
      <h4 id="duplicate-audit-title">Canonical direction</h4>
    </div>
    <dl className="duplicate-direction-grid">
      <div><dt>Direct destination</dt><dd><bdi dir="auto">{context.canonical.direct_destination?.title}</bdi><span className="mono break-all">{context.canonical.direct_destination?.id}</span></dd></div>
      <div><dt>Current canonical root</dt><dd><bdi dir="auto">{context.canonical.canonical_work_item.title}</bdi><span className="mono break-all">{context.canonical.canonical_work_item.id}</span></dd></div>
    </dl>
    {context.canonical.path.length > 1 && <ol className="duplicate-hop-path" aria-label="Canonical merge path">{context.canonical.path.map((hop) => <li key={hop.id}><bdi dir="auto">{hop.title}</bdi><span className="mono break-all">{hop.id}</span></li>)}</ol>}
    <p>{context.duplicate_member_total} immutable duplicate audit record{context.duplicate_member_total === 1 ? "" : "s"} in this canonical group{context.omitted_duplicate_member_count ? `; ${context.omitted_duplicate_member_count} omitted from this bounded view` : ""}.</p>
    {mergeEvent && <dl className="duplicate-merge-fact">
      <div><dt>Merge rationale</dt><dd>{mergeEvent.body}</dd></div>
      <div><dt>Recorded</dt><dd>{formatDateTime(mergeEvent.created_at)}</dd></div>
      <div><dt>Provenance</dt><dd>{mergeEvent.actor_client} · {mergeEvent.actor_session_id}{mergeEvent.actor_model ? ` · ${mergeEvent.actor_model}` : ""}</dd></div>
    </dl>}
    <div className="duplicate-audit-actions">
      <button type="button" className={`button button-secondary ${copiedKey === auditKey ? "is-copied" : ""}`} onClick={() => onCopy(context.work_item.id, auditKey, "Duplicate audit ID copied.")}><Icon name="copy" size={16} />Copy audit ID</button>
      <button type="button" className="button button-primary" onClick={() => onOpenCanonical(context.canonical.canonical_work_item.id)}>Open canonical work</button>
      <button type="button" className="button button-secondary" onClick={() => onViewDuplicateGroup(context.canonical.canonical_work_item.id)}>View duplicate group</button>
    </div>
  </section>;
}

function ContextTab({ context, isDuplicate, props }: { context: WorkContext; isDuplicate: boolean; props: WorkDetailPaneProps }) {
  if (props.mode === "edit" && props.editDraft && !isDuplicate) {
    return <div className="detail-edit">
      <WorkItemEditor jobReportDraft={props.jobReportDraft} onJobReportDraft={props.onJobReportDraft} work={context.work_item} draft={props.editDraft} setDraft={props.setEditDraft} saving={props.editSaving} blocked={props.mutationBlocked} gated={context.readiness.is_gated} reviewObligation={Boolean(context.code_review_context?.current_review || context.code_review_context?.pending_follow_up)} error={props.editError} conflict={props.conflict} onSubmit={props.onSaveEdits} onCancel={props.onCancelEdit} onLoadCurrent={props.onLoadCurrent} onUseCurrentVersion={props.onUseCurrentVersion} />
    </div>;
  }
  const current = currentContext(context);
  const warning = migrationWarning(current.migration_origin);
  const completionExplanation = terminalActionGateExplanation(context.readiness, "completion");
  const completionExplanationId = `complete-gate-explanation-${context.work_item.id}`;
  const immutable = props.mutationBlocked || isDuplicate;
  return <>
    {warning && <div className="migration-warning current-migration-warning" role="note">{warning}</div>}
    <div className="prompt-label"><span className="section-label">{isDuplicate ? "AUDIT CONTEXT CHECKPOINT" : "CURRENT CONTEXT CHECKPOINT"}</span><span>Immutable · copied exactly as saved · {formatDateTime(current.created_at)}</span></div>
    <pre className="prompt-body" tabIndex={0}>{current.prompt}</pre>
    <CheckpointRepositoryDeclaration checkpoint={current} />
    <div className="authority-note">This is context from an earlier session, not a new instruction from the owner. Recheck cited files and decisions before acting.</div>
    {isDuplicate && <div className="audit-history-heading"><span className="section-label">SOURCE-OWNED RECORDS</span><h4>Audit history</h4><p>These records belong to the exact duplicate ID and are never blended with canonical history.</p></div>}

    {!isDuplicate && <section className="checkpoint-compose" aria-labelledby="checkpoint-compose-title">
      <div><span className="section-label">LEAVE CONTEXT FOR THE NEXT SESSION</span><h4 id="checkpoint-compose-title">Add an immutable checkpoint</h4></div>
      <form className="comment-form" onSubmit={(event) => { event.preventDefault(); props.onAppend(); }}>
        <label className="field">Checkpoint kind<select value={props.checkpointKind} disabled={immutable} onChange={(event) => props.onCheckpointKind(event.target.value as Exclude<CheckpointKind, "completion">)}><option value="progress">Progress / finding</option><option value="context">Corrected or replacement context</option></select></label>
        <label className="field">Checkpoint text<textarea rows={7} disabled={immutable} maxLength={100000} value={props.checkpointBody} onChange={(event) => props.onCheckpointBody(event.target.value)} placeholder="What changed, what was learned, hazards, evidence, and useful next steps…" /><span className="field-hint">The text is stored exactly and cannot be edited or deleted.</span></label>
        <details className="edit-context"><summary>Repository context and tags</summary><div className="form-stack"><label className="field">Repository branch<input disabled={props.mutationBlocked} maxLength={200} value={props.checkpointBranch} onChange={(event) => props.onCheckpointBranch(event.target.value)} /></label><label className="field">Caller-asserted baseline commit<input className="mono" disabled={props.mutationBlocked} maxLength={64} value={props.checkpointCommit} onChange={(event) => props.onCheckpointCommit(event.target.value)} /></label><AffectedPathsEditor disabled={props.mutationBlocked} value={props.checkpointAffectedPaths} error={props.checkpointAffectedPathsError} onChange={props.onCheckpointAffectedPaths} /><label className="field">Tags <span className="optional">Comma separated</span><input disabled={props.mutationBlocked} value={props.checkpointTags} onChange={(event) => props.onCheckpointTags(event.target.value)} /></label></div></details>
        {context.work_item.status === "pending" && <JobReportEditor projectId={context.work_item.project_id} draft={props.jobReportDraft} onChange={props.onJobReportDraft} disabled={props.checkpointSaving || immutable} />}
        {context.work_item.status === "pending" && <details className="edit-context completion-evidence-disclosure">
          <summary>Completion evidence <span className="optional">Optional · used only by Complete work</span></summary>
          <CompletionEvidenceEditor
            draft={props.completionEvidenceDraft}
            issues={props.completionEvidenceIssues}
            disabled={props.checkpointSaving || immutable}
            onChange={props.onCompletionEvidenceDraft}
          />
        </details>}
        {props.checkpointActionError && <div className="error-notice" role="alert"><p>{props.checkpointActionError}</p></div>}
        <div className="comment-actions"><button type="submit" className="button button-secondary" disabled={props.checkpointSaving || props.mutationBlocked || !props.checkpointBody.trim()}>{props.checkpointSaving ? "Saving…" : "Add checkpoint"}</button>{context.work_item.status === "pending" && <button type="button" className="button button-primary" title={completionExplanation ?? undefined} aria-describedby={completionExplanation ? completionExplanationId : undefined} disabled={props.checkpointSaving || terminalActionDisabled(context.readiness, props.mutationBlocked, "completion") || props.jobReportDraft.promptRevision === null || !props.checkpointBody.trim()} onClick={props.onComplete}>{props.checkpointSaving ? "Saving…" : "Complete work"}<Icon name="check" size={16} /></button>}{context.work_item.status === "pending" && completionExplanation && <p className="terminal-action-note" id={completionExplanationId}>{completionExplanation}</p>}</div>
      </form>
    </section>}
  </>;
}

function TabBody({ context, isDuplicate, props }: { context: WorkContext; isDuplicate: boolean; props: WorkDetailPaneProps }) {
  switch (props.tab) {
    case "context":
      return <ContextTab context={context} isDuplicate={isDuplicate} props={props} />;
    case "history":
      return <>
        <WorkReportProvenance projectId={context.work_item.project_id} workItemId={context.work_item.id} refreshSignal={props.eventRefreshSignal} onOpenWork={props.onOpenCanonical} />
        <CheckpointTimeline page={props.checkpointPage} offset={props.checkpointOffset} currentCheckpointId={currentContext(context).id} loading={props.checkpointLoading} error={props.checkpointLoadError} onOffset={props.onCheckpointOffset} onReload={props.onReloadCheckpoints} />
        <section className="context-section"><div className="section-label">WORK RECORD</div><dl className="metadata-grid"><div><dt>Created</dt><dd>{formatDateTime(context.work_item.created_at)}</dd></div><div><dt>Last activity</dt><dd>{formatDateTime(context.work_item.updated_at)}</dd></div><div><dt>Checkpoints</dt><dd>{context.checkpoint_total}</dd></div><div><dt>Omitted from bounded recall</dt><dd>{context.omitted_checkpoint_count}</dd></div><div className="span-two"><dt>Work item ID</dt><dd className="mono break-all">{context.work_item.id}</dd></div></dl></section>
      </>;
    case "evidence":
      return <CompletionEvidencePanel
        projectId={context.work_item.project_id}
        workItemId={context.work_item.id}
        refreshSignal={props.evidenceRefreshSignal}
      />;
    case "graph":
      return <>
        {/* Merge mode only opens on a canonical root, but a lost merge response followed by a
            live-sync reload can turn the open source into a duplicate while its merge intent is
            still unresolved. The panel stays mounted so its recovery block and exact retry survive. */}
        {props.mergeOpen && <WorkMergePanel source={context} recoveryVisible={props.mergeRecoveryVisible} onClose={props.onCloseMerge} onMerged={props.onMerged} onSourceChanged={props.onMergeSourceChanged} />}
        <RelationshipPanel
          context={context}
          projects={props.projects}
          onChanged={props.onRelationshipsChanged}
          onOpenWork={props.onOpenCanonical}
        />
      </>;
    case "questions":
      return <HumanGatePanel context={context} refreshSignal={props.eventRefreshSignal} onResolved={props.onGateResolved} />;
    case "reviews":
      return <CodeReviewPanel key={context.work_item.id} context={context} allowRemediationReviews={props.allowRemediationReviews} refreshSignal={props.eventRefreshSignal} onChanged={props.onReviewChanged} onOpen={props.onOpenCanonical} />;
    case "activity":
      return <WorkEventTimeline context={context} refreshSignal={props.eventRefreshSignal} onAppended={props.onEventAppended} />;
  }
}

export function StatusActionButton({
  summary,
  projects,
  disabled,
  busy,
  reportSettingsReady,
  moveDisabled,
  moving,
  moveTitle,
  moveExplanationId,
  onAction,
  onMove,
  compact = false
}: {
  summary: WorkSummary;
  projects: readonly Project[];
  disabled: boolean;
  busy: boolean;
  reportSettingsReady: boolean;
  moveDisabled: boolean;
  moving: boolean;
  moveTitle: string;
  moveExplanationId?: string;
  onAction: (action: ManualStatusAction, summary: WorkSummary) => void;
  onMove: (targetProjectId: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [moveMenuStyle, setMoveMenuStyle] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    right: "auto",
    visibility: "hidden"
  });
  const [compactMenuStyle, setCompactMenuStyle] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    right: "auto",
    bottom: "auto",
    visibility: "hidden"
  });
  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const moveRootRef = useRef<HTMLDivElement>(null);
  const moveItemRef = useRef<HTMLButtonElement>(null);
  const moveMenuRef = useRef<HTMLDivElement>(null);
  const moveCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const moveBlurFrame = useRef<number | null>(null);
  const restoreMoveFocusFrame = useRef<number | null>(null);
  const pointerWithinMove = useRef(false);
  const suppressMoveFocusOpen = useRef(false);
  const moveTargetFocused = useRef(false);
  const openingFocus = useRef<"first" | "last">("first");
  const menuId = useId();
  const moveItemId = useId();
  const moveMenuId = useId();
  const { work_item: work, readiness } = summary;
  const actions = availableStatusActions(work.status, readiness);
  const targetProjects = projects.filter((project) => project.id !== work.project_id);
  const targetProjectKey = targetProjects.map((project) => project.id).join(":");
  const moveLayoutKey = JSON.stringify({
    actions: actions.map((action) => action.value),
    projects: targetProjects.map((project) => [project.id, project.name, project.slug])
  });
  const moveUnavailable = moveDisabled || moving || targetProjects.length === 0;
  const controlsBusy = busy || moving;
  const primaryDisabled = disabled || controlsBusy || work.status === "deferred";

  useEffect(() => {
    setOpen(false);
    closeMoveMenu();
    cancelRestoreMoveFocus();
    suppressMoveFocusOpen.current = false;
  }, [work.id, work.project_id]);
  useEffect(() => {
    if (disabled || controlsBusy) {
      setOpen(false);
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
    }
  }, [controlsBusy, disabled]);
  useEffect(() => {
    const restoreMoveFocus = moveTargetFocused.current;
    if (!moveUnavailable && !restoreMoveFocus) return;
    closeMoveMenu();
    cancelRestoreMoveFocus();
    if (restoreMoveFocus) {
      const trigger = moveItemRef.current;
      restoreMoveFocusFrame.current = requestAnimationFrame(() => {
        restoreMoveFocusFrame.current = null;
        if (!trigger?.isConnected || moveItemRef.current !== trigger) {
          suppressMoveFocusOpen.current = false;
          return;
        }
        suppressMoveFocusOpen.current = true;
        trigger.focus();
        suppressMoveFocusOpen.current = false;
      });
    }
    return cancelRestoreMoveFocus;
  }, [moveUnavailable, targetProjectKey]);
  useEffect(() => {
    if (!open) {
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
      return;
    }
    const closeCascade = () => {
      setOpen(false);
      closeMoveMenu();
      cancelRestoreMoveFocus();
      suppressMoveFocusOpen.current = false;
    };
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !rootRef.current?.contains(target)
        && !moveMenuRef.current?.contains(target)
      ) closeCascade();
    };
    document.addEventListener("pointerdown", closeOutside);
    window.addEventListener("blur", closeCascade);
    const items = mainMenuItems();
    const target = openingFocus.current === "last" ? items.at(-1) : items[0];
    target?.focus();
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      window.removeEventListener("blur", closeCascade);
    };
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !compact) return;
    const trigger = rootRef.current;
    const menu = menuRef.current;
    if (!trigger || !menu) return;
    const positionMenu = (event?: Event) => {
      if (event?.target instanceof Node && menu.contains(event.target)) return;
      const triggerRect = trigger.getBoundingClientRect();
      const scrollPane = trigger.closest<HTMLElement>(".work-queue-list");
      const paneRect = scrollPane?.getBoundingClientRect();
      const visibleTop = Math.max(0, paneRect?.top ?? 0);
      const visibleRight = Math.min(window.innerWidth, paneRect?.right ?? window.innerWidth);
      const visibleBottom = Math.min(window.innerHeight, paneRect?.bottom ?? window.innerHeight);
      const visibleLeft = Math.max(0, paneRect?.left ?? 0);
      if (
        triggerRect.bottom <= visibleTop
        || triggerRect.left >= visibleRight
        || triggerRect.top >= visibleBottom
        || triggerRect.right <= visibleLeft
      ) {
        setOpen(false);
        return;
      }
      const viewportMargin = 16;
      const menuGap = 6;
      const availableWidth = Math.max(155, window.innerWidth - viewportMargin * 2);
      const width = Math.min(Math.max(155, menu.scrollWidth), availableWidth);
      const maxHeight = Math.min(320, Math.max(80, window.innerHeight - viewportMargin * 2));
      const height = Math.min(menu.scrollHeight, maxHeight);
      const roomBelow = window.innerHeight - triggerRect.bottom - viewportMargin - menuGap;
      const roomAbove = triggerRect.top - viewportMargin - menuGap;
      const opensDown = roomBelow >= height || roomBelow >= roomAbove;
      const top = opensDown
        ? Math.min(triggerRect.bottom + menuGap, window.innerHeight - height - viewportMargin)
        : Math.max(viewportMargin, triggerRect.top - height - menuGap);
      const left = Math.min(
        Math.max(viewportMargin, triggerRect.left),
        Math.max(viewportMargin, window.innerWidth - width - viewportMargin)
      );
      setCompactMenuStyle((current) => (
        current.top === top
        && current.left === left
        && current.width === width
        && current.maxHeight === maxHeight
        && current.visibility === "visible"
          ? current
          : {
            position: "fixed",
            top,
            left,
            right: "auto",
            bottom: "auto",
            width,
            maxHeight,
            overflowY: "auto",
            visibility: "visible"
          }
      ));
    };
    positionMenu();
    window.addEventListener("resize", positionMenu);
    document.addEventListener("scroll", positionMenu, true);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => positionMenu());
    resizeObserver?.observe(trigger);
    resizeObserver?.observe(menu);
    return () => {
      window.removeEventListener("resize", positionMenu);
      document.removeEventListener("scroll", positionMenu, true);
      resizeObserver?.disconnect();
    };
  }, [compact, moveLayoutKey, open]);
  useEffect(() => () => {
    cancelMoveClose();
    cancelMoveBlur();
    cancelRestoreMoveFocus();
  }, []);
  useLayoutEffect(() => {
    if (!moveOpen) return;
    const trigger = moveItemRef.current;
    const submenu = moveMenuRef.current;
    if (!trigger || !submenu) return;
    const positionMenu = (event?: Event) => {
      if (event?.target instanceof Node && submenu.contains(event.target)) return;
      const triggerRect = trigger.getBoundingClientRect();
      const scrollPane = trigger.closest<HTMLElement>(".detail-scroll");
      const paneRect = scrollPane?.getBoundingClientRect();
      const visibleTop = Math.max(0, paneRect?.top ?? 0);
      const visibleRight = Math.min(window.innerWidth, paneRect?.right ?? window.innerWidth);
      const visibleBottom = Math.min(window.innerHeight, paneRect?.bottom ?? window.innerHeight);
      const visibleLeft = Math.max(0, paneRect?.left ?? 0);
      if (
        triggerRect.bottom <= visibleTop
        || triggerRect.left >= visibleRight
        || triggerRect.top >= visibleBottom
        || triggerRect.right <= visibleLeft
      ) {
        closeMoveMenu();
        return;
      }
      const availableWidth = Math.max(160, window.innerWidth - 32);
      // Keep the flyout width independent from its currently rendered width.
      // Measuring scrollWidth while also observing the submenu creates a resize
      // feedback loop on narrow viewports, which makes the menu move under the
      // pointer and can close it before a project is selected.
      const width = Math.min(320, availableWidth);
      const maxHeight = Math.min(320, Math.max(120, window.innerHeight - 32));
      const height = Math.min(submenu.scrollHeight, maxHeight);
      const opensRight = window.innerWidth - triggerRect.right - 16 >= width;
      const left = opensRight
        ? triggerRect.right - 1
        : Math.max(16, triggerRect.left - width + 1);
      const top = Math.min(
        Math.max(16, triggerRect.top - 5),
        Math.max(16, window.innerHeight - height - 16)
      );
      setMoveMenuStyle((current) => (
        current.top === top
        && current.left === left
        && current.width === width
        && current.maxHeight === maxHeight
        && current.visibility === "visible"
          ? current
          : {
            position: "fixed",
            top,
            left,
            right: "auto",
            width,
            maxHeight,
            visibility: "visible"
          }
      ));
    };
    positionMenu();
    window.addEventListener("resize", positionMenu);
    document.addEventListener("scroll", positionMenu, true);
    const resizeObserver = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => positionMenu());
    if (resizeObserver) {
      resizeObserver.observe(trigger);
      if (menuRef.current) resizeObserver.observe(menuRef.current);
    }
    return () => {
      window.removeEventListener("resize", positionMenu);
      document.removeEventListener("scroll", positionMenu, true);
      resizeObserver?.disconnect();
    };
  }, [moveLayoutKey, moveOpen]);

  function mainMenuItems(): HTMLButtonElement[] {
    return [...(menuRef.current?.querySelectorAll<HTMLButtonElement>(
      `[data-status-menu-item="true"]`
    ) ?? [])].filter((item) => !item.disabled);
  }

  function moveMenuItems(): HTMLButtonElement[] {
    return [...(moveMenuRef.current?.querySelectorAll<HTMLButtonElement>(
      ":scope > button:not(:disabled)"
    ) ?? [])];
  }

  function cancelMoveClose(): void {
    if (moveCloseTimer.current !== null) {
      clearTimeout(moveCloseTimer.current);
      moveCloseTimer.current = null;
    }
  }

  function cancelMoveBlur(): void {
    if (moveBlurFrame.current !== null) {
      cancelAnimationFrame(moveBlurFrame.current);
      moveBlurFrame.current = null;
    }
  }

  function cancelRestoreMoveFocus(): void {
    if (restoreMoveFocusFrame.current !== null) {
      cancelAnimationFrame(restoreMoveFocusFrame.current);
      restoreMoveFocusFrame.current = null;
    }
  }

  function closeMoveMenu(): void {
    cancelMoveClose();
    cancelMoveBlur();
    pointerWithinMove.current = false;
    moveTargetFocused.current = false;
    setMoveOpen(false);
  }

  function checkMoveBlur(): void {
    cancelMoveBlur();
    moveBlurFrame.current = requestAnimationFrame(() => {
      moveBlurFrame.current = null;
      const active = document.activeElement;
      const focusInRoot = moveRootRef.current?.contains(active) ?? false;
      const focusInMenu = moveMenuRef.current?.contains(active) ?? false;
      moveTargetFocused.current = focusInMenu;
      if (
        !focusInRoot
        && !focusInMenu
        && !pointerWithinMove.current
      ) closeMoveMenu();
    });
  }

  function scheduleMoveClose(): void {
    cancelMoveClose();
    moveCloseTimer.current = setTimeout(() => {
      moveCloseTimer.current = null;
      const active = document.activeElement;
      if (
        !moveRootRef.current?.contains(active)
        && !moveMenuRef.current?.contains(active)
      ) closeMoveMenu();
    }, 180);
  }

  function openMoveAndFocus(position: "first" | "last" = "first"): void {
    if (moveUnavailable) return;
    cancelMoveClose();
    setMoveOpen(true);
    requestAnimationFrame(() => {
      const items = moveMenuItems();
      (position === "last" ? items.at(-1) : items[0])?.focus();
    });
  }

  function closeAndFocus(): void {
    setOpen(false);
    closeMoveMenu();
    toggleRef.current?.focus();
  }

  function leaveCascadeWithTab(backwards: boolean): void {
    const chooser = toggleRef.current;
    const root = rootRef.current;
    if (!chooser || !root) {
      setOpen(false);
      closeMoveMenu();
      return;
    }
    const focusable = [...document.querySelectorAll<HTMLElement>(
      "button:not(:disabled):not([tabindex=\"-1\"]), a[href], input:not(:disabled), "
      + "select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex=\"-1\"])"
    )].filter((element) => element.getClientRects().length > 0);
    const chooserIndex = focusable.indexOf(chooser);
    const destination = backwards
      ? focusable.slice(0, chooserIndex).at(-1)
      : focusable.slice(chooserIndex + 1).find((element) => !root.contains(element));
    setOpen(false);
    closeMoveMenu();
    destination?.focus();
  }

  function moveMenuFocus(event: KeyboardEvent<HTMLDivElement>): void {
    if (moveMenuRef.current?.contains(event.target as Node)) return;
    const items = mainMenuItems();
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = current < items.length - 1 ? current + 1 : 0;
    if (event.key === "ArrowUp") next = current > 0 ? current - 1 : items.length - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== null) {
      event.preventDefault();
      event.stopPropagation();
      items[next]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeAndFocus();
    } else if (event.key === "Tab") {
      event.preventDefault();
      event.stopPropagation();
      leaveCascadeWithTab(event.shiftKey);
    }
  }

  function moveProjectFocus(event: KeyboardEvent<HTMLDivElement>): void {
    const items = moveMenuItems();
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let next: number | null = null;
    if (event.key === "ArrowDown") next = current < items.length - 1 ? current + 1 : 0;
    if (event.key === "ArrowUp") next = current > 0 ? current - 1 : items.length - 1;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = items.length - 1;
    if (next !== null) {
      event.preventDefault();
      event.stopPropagation();
      items[next]?.focus();
    } else if (event.key === "ArrowLeft" || event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMoveMenu();
      suppressMoveFocusOpen.current = true;
      moveItemRef.current?.focus();
      suppressMoveFocusOpen.current = false;
    } else if (event.key === "Tab") {
      event.preventDefault();
      event.stopPropagation();
      leaveCascadeWithTab(event.shiftKey);
    }
  }

  return <div
    className={`status-split-button ${compact ? "queue-status-split-button" : ""}`}
    ref={rootRef}
    onClick={(event) => event.stopPropagation()}
  >
    <button
      className="button defer-button status-split-primary"
      type="button"
      disabled={primaryDisabled}
      aria-label={`Defer ${work.title}`}
      title={work.status === "deferred"
        ? "This work item is already Deferred. Choose another status from the menu."
        : "Explicitly hold this work item out of the work queue"}
      onClick={() => onAction("defer", summary)}
    >{moving ? "Moving…" : busy ? "Saving…" : "Defer"}</button>
    <button
      ref={toggleRef}
      className="button defer-button status-split-toggle"
      type="button"
      disabled={disabled || controlsBusy}
      aria-label={`Choose an action for ${work.title}`}
      aria-haspopup="menu"
      aria-expanded={open}
      aria-controls={menuId}
      onClick={() => {
        openingFocus.current = "first";
        setOpen((value) => !value);
      }}
      onKeyDown={(event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          event.stopPropagation();
          openingFocus.current = event.key === "ArrowUp" ? "last" : "first";
          setOpen(true);
        }
      }}
    ><span aria-hidden="true">⌄</span></button>
    {open && <div
      ref={menuRef}
      className="status-action-menu"
      style={compact ? compactMenuStyle : undefined}
      id={menuId}
      role="menu"
      aria-label={`Actions for ${work.title}`}
      aria-owns={moveOpen ? moveMenuId : undefined}
      onKeyDown={moveMenuFocus}
    >{actions.map((action) => {
      const reason = statusActionDisabledReason(
        action.value,
        readiness,
        reportSettingsReady
      );
      return <button
        type="button"
        role="menuitem"
        tabIndex={-1}
        key={action.value}
        data-status-menu-item="true"
        disabled={Boolean(reason)}
        title={reason ?? `Explicitly mark this work item ${action.label}`}
        aria-label={`${action.label} ${work.title}`}
        onFocus={closeMoveMenu}
        onClick={() => {
          setOpen(false);
          closeMoveMenu();
          onAction(action.value, summary);
        }}
      >{action.label}</button>;
    })}
    <div role="separator" className="status-action-separator" />
    <div
      ref={moveRootRef}
      className="status-move-menu-item"
      role="none"
      onPointerEnter={() => {
        pointerWithinMove.current = true;
        cancelMoveClose();
        if (!moveUnavailable) setMoveOpen(true);
      }}
      onPointerLeave={() => {
        pointerWithinMove.current = false;
        scheduleMoveClose();
      }}
      onBlur={checkMoveBlur}
    >
      <button
        ref={moveItemRef}
        id={moveItemId}
        type="button"
        role="menuitem"
        tabIndex={-1}
        data-status-menu-item="true"
        aria-label={`Move ${work.title} to another project`}
        aria-haspopup="menu"
        aria-expanded={moveOpen}
        aria-controls={moveMenuId}
        aria-disabled={moveUnavailable}
        aria-describedby={moveExplanationId}
        title={moveTitle}
        onFocus={() => {
          cancelMoveClose();
          if (suppressMoveFocusOpen.current) {
            suppressMoveFocusOpen.current = false;
          } else if (!moveUnavailable) {
            setMoveOpen(true);
          }
        }}
        onClick={(event) => {
          if (moveUnavailable) {
            event.preventDefault();
            return;
          }
          openMoveAndFocus();
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowRight" || event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            event.stopPropagation();
            openMoveAndFocus();
          }
        }}
      ><span>{moving ? "Moving…" : "Move"}</span><span aria-hidden="true">›</span></button>
      {moveOpen && createPortal(<div
        ref={moveMenuRef}
        style={moveMenuStyle}
        className="status-action-menu status-action-submenu move-project-menu"
        id={moveMenuId}
        role="menu"
        aria-label={`Move ${work.title} to project`}
        onPointerDownCapture={() => {
          pointerWithinMove.current = true;
        }}
        onPointerEnter={() => {
          pointerWithinMove.current = true;
          cancelMoveClose();
        }}
        onPointerLeave={() => {
          pointerWithinMove.current = false;
          scheduleMoveClose();
        }}
        onKeyDown={moveProjectFocus}
      >{targetProjects.map((project) => <button
        type="button"
        role="menuitem"
        tabIndex={-1}
        key={project.id}
        aria-label={`${project.name} (${project.slug})`}
        onFocus={() => {
          moveTargetFocused.current = true;
        }}
        onBlur={checkMoveBlur}
        onClick={() => {
          if (moveUnavailable) return;
          closeMoveMenu();
          setOpen(false);
          onMove(project.id);
        }}
      ><span className="move-project-identity"><bdi dir="auto">{project.name}</bdi>
          <small>{project.slug}</small></span></button>)}</div>, document.body)}
    </div>
    </div>}
  </div>;
}

function OpenedPane({ opened, props }: { opened: WorkSummary; props: WorkDetailPaneProps }) {
  // A context is only trusted for the header when it belongs to the selection;
  // the dashboard sets both together, so a mismatch renders as still loading.
  const context = props.context && props.context.work_item.id === opened.work_item.id
    ? props.context
    : null;
  const work = context?.work_item ?? opened.work_item;
  const readiness = context?.readiness ?? opened.readiness;
  const current = context ? currentContext(context) : opened.current_context;
  const isDuplicate = context ? context.canonical.is_duplicate : opened.readiness.is_duplicate;
  const pointerSummary: WorkSummary = context
    ? {
      ...opened,
      work_item: context.work_item,
      checkpoint_count: context.checkpoint_total,
      current_context: currentContext(context),
      readiness: context.readiness
    }
    : opened;
  const idKey = copyKey(work.id, "id");
  const pointerKey = copyKey(work.id, "pointer");
  const contextKey = copyKey(work.id, "context");
  const reviewKey = copyKey(work.id, "cold-review");
  const currentReview = context?.code_review_context?.current_review;
  const reviewObligation = Boolean(currentReview || context?.code_review_context?.pending_follow_up);
  const canonicalKey = copyKey(work.id, "canonical-id");
  const idCopied = props.copiedKey === idKey;
  const deleteExplanation = reviewObligation ? "Reopen work to explicitly supersede the outstanding review or recommendation before deletion." : terminalActionGateExplanation(readiness, "deletion");
  const deleteExplanationId = `detail-delete-gate-explanation-${work.id}`;
  const moveDisabledReason = workMoveDisabledReason(context, props.mutationBlocked);
  const hasMoveTarget = props.projects.some((project) => project.id !== work.project_id);
  const moveExplanation = moveDisabledReason
    ?? (!hasMoveTarget ? "Create another project before moving this work item." : null);
  const moveExplanationId = `detail-move-explanation-${work.id}`;
  const mergeLeaseExplanation = reviewObligation ? "Reopen work to supersede the outstanding review or recommendation before merging."
    : context?.code_review_context?.remediation_depth ? "Review remediation work cannot be merged as a duplicate; its provenance must remain intact."
    : context?.duplicate_merge_eligibility.source_lease_state === "active"
    ? "Release the source’s active lease, or wait for it to expire, before merging in the browser."
    : "";
  const mergeLeaseExplanationId = `merge-lease-explanation-${work.id}`;
  const actionsLocked = !context || props.mutationBlocked;
  const reconciling = props.reconciliationRequired && props.contextLoading;
  const tabs = detailTabs(context, opened);

  return <>
    <div className="detail-header">
      {props.recovery}
      {props.notice}
      <div className="detail-identity">
        <button type="button" className="icon-button detail-back" aria-label="Back to work queue" disabled={props.backDisabled} onClick={props.onBack}><Icon name="back" /></button>
        <StatusBadge status={work.status} readiness={readiness} />
        <OperationalBadge readiness={readiness} />
        <span className="detail-version" title="Work item version">v{work.version}</span>
        <span className="detail-id"><code>{work.id}</code><button type="button" className={`icon-button detail-copy-id ${idCopied ? "is-copied" : ""}`} aria-label="Copy work item ID" title={idCopied ? "Copied" : "Copy work item ID"} onClick={() => props.onCopy(work.id, idKey, `Work item ID copied: ${work.id}`)}><Icon name={idCopied ? "check" : "copy"} size={13} /></button></span>
        <span className="detail-activity">Last activity <time dateTime={work.updated_at}>{formatDateTime(work.updated_at)}</time></span>
      </div>
      <h3 className="detail-title">{work.title}</h3>
      <p className="detail-summary">{work.summary}</p>
      <ExternalReferences references={work.external_references} />
      <dl className="detail-facts" aria-label="Work item facts">
        <div><dt>Priority</dt><dd>{work.priority}</dd></div>
        <div><dt>Checkpoints</dt><dd>{context?.checkpoint_total ?? opened.checkpoint_count}</dd></div>
        <div><dt>Current context</dt><dd>{clientLabel(current.source_client)}</dd></div>
        <div><dt>Session</dt><dd className="mono" title={current.source_session_id}>{current.source_session_id}</dd></div>
        <div className="detail-fact-tags"><dt>Tags</dt><dd>{current.tags.length ? current.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>) : <span className="detail-fact-none">None</span>}</dd></div>
      </dl>
      {reconciling && <div className="detail-reconciliation-status" role="status"><span className="spinner" />Reconciling saved work context…</div>}
      {context && isDuplicate && <DuplicateAuditPanel context={context} copiedKey={props.copiedKey} onCopy={props.onCopy} onOpenCanonical={props.onOpenCanonical} onViewDuplicateGroup={props.onViewDuplicateGroup} />}
      {readiness.active_lease && <ActiveLeaseSummary lease={readiness.active_lease} detailed />}
      <div className="detail-actions">
        <button type="button" className={`button button-primary ${props.copiedKey === pointerKey ? "is-copied" : ""}`} onClick={() => props.onCopyPointer(pointerSummary)}><Icon name="copy" size={16} />{props.copiedKey === pointerKey ? "Copied" : "Copy recall pointer"}</button>
        {currentReview ? <button type="button" className={`button button-primary detail-cold-review ${props.copiedKey === reviewKey ? "is-copied" : ""}`} aria-label="Copy cold review prompt" disabled={!context || props.contextLoading} onClick={props.onCopyColdReview}><Icon name="copy" size={16} />{props.copiedKey === reviewKey ? "Copied" : "Cold review"}</button> : <button type="button" className={`button copy-button detail-copy-context ${props.copiedKey === contextKey ? "is-copied" : ""}`} aria-label="Copy current context" disabled={!context} onClick={() => { if (context) props.onCopy(currentContext(context).prompt, contextKey, "Current context copied exactly as stored."); }}><Icon name="copy" size={16} />{props.copiedKey === contextKey ? "Copied" : "Copy context"}</button>}
        {reviewObligation && <button type="button" className="button button-secondary" disabled={actionsLocked} onClick={props.onReopenReview}>Reopen work…</button>}
        {!isDuplicate && <button type="button" className="button button-secondary" aria-label="Edit work item" disabled={actionsLocked} onClick={props.onEdit}>Edit</button>}
        {!isDuplicate && <button type="button" className="button button-secondary" title={mergeLeaseExplanation || undefined} aria-describedby={mergeLeaseExplanation ? mergeLeaseExplanationId : undefined} disabled={actionsLocked || Boolean(mergeLeaseExplanation)} onClick={props.onOpenMerge}>Merge as duplicate…</button>}
        {!isDuplicate && <StatusActionButton
          summary={pointerSummary}
          projects={props.projects}
          disabled={actionsLocked || reviewObligation}
          busy={props.statusChanging}
          reportSettingsReady={props.reportSettingsReady}
          moveDisabled={Boolean(moveDisabledReason)}
          moving={props.moving}
          moveTitle={moveExplanation ?? "Move this work item to another project"}
          moveExplanationId={moveExplanation ? moveExplanationId : undefined}
          onAction={props.onStatusAction}
          onMove={props.onMove}
        />}
        {!isDuplicate && context && context.duplicate_member_total > 0 && <button type="button" className={`button button-secondary ${props.copiedKey === canonicalKey ? "is-copied" : ""}`} onClick={() => props.onCopy(work.id, canonicalKey, "Canonical work ID copied.")}><Icon name="copy" size={16} />Copy canonical ID</button>}
        {!isDuplicate && <button
          type="button"
          className="button detail-delete"
          aria-label="Delete work item"
          title={deleteExplanation || "Delete work item"}
          aria-describedby={deleteExplanation ? deleteExplanationId : undefined}
          disabled={!context || reviewObligation || props.moving || terminalActionDisabled(readiness, props.mutationBlocked)}
          onClick={props.onDelete}
        >Delete</button>}
        {!isDuplicate && deleteExplanation && <p className="terminal-action-note" id={deleteExplanationId}>
          {deleteExplanation}
        </p>}
        {!isDuplicate && moveExplanation && <p className="terminal-action-note" id={moveExplanationId}>
          {moveExplanation}
        </p>}
        {mergeLeaseExplanation && <p className="terminal-action-note" id={mergeLeaseExplanationId}>{mergeLeaseExplanation}</p>}
      </div>
    </div>
    <div className="detail-tabs" role="tablist" aria-label="Work context sections">
      {tabs.map((tab) => {
        const selected = props.tab === tab.key;
        return <button type="button" role="tab" key={tab.key} id={`detail-tab-${tab.key}`} aria-selected={selected} aria-controls={`detail-panel-${tab.key}`} className={`detail-tab ${selected ? "is-selected" : ""}`} onClick={() => props.onTab(tab.key)}>{tab.label}{tab.count !== undefined && <span className={`detail-tab-count ${tab.alert ? "is-alert" : ""}`}>{tab.count}</span>}</button>;
      })}
    </div>
    <div className="detail-tab-body" role="tabpanel" id={`detail-panel-${props.tab}`} aria-labelledby={`detail-tab-${props.tab}`}>
      {!context
        ? props.contextError
          ? <ContextRetry message={props.contextError} onRetry={props.onRetryContext} />
          : props.contextLoading || !props.reconciliationRequired
            ? <div className="loading-state detail-loading" role="status"><span className="spinner" />{props.reconciliationRequired ? "Reconciling saved work context…" : "Recalling work context…"}</div>
            : <ContextRetry message="The saved mutation could not be reconciled with current work context." onRetry={props.onRetryContext} />
        : <div className="detail-reconciliation-frame" key={work.id} aria-busy={reconciling} inert={reconciling}>
          {props.contextError && <ContextRetry message={props.contextError} onRetry={props.onRetryContext} />}
          <TabBody context={context} isDuplicate={isDuplicate} props={props} />
        </div>}
    </div>
  </>;
}

export default function WorkDetailPane(props: WorkDetailPaneProps) {
  const { opened } = props;
  const openedId = opened?.work_item.id ?? null;
  const [motion, setMotion] = useState<CSSProperties>(SLIDE_END);
  const scrollRef = useRef<HTMLDivElement>(null);
  const shownId = useRef<string | null>(null);

  // Replays only when the selected work item changes; reloading the same item
  // (live sync, reconciliation) keeps the pane where it is.
  useLayoutEffect(() => {
    const previous = shownId.current;
    if (openedId === previous) return;
    shownId.current = openedId;
    if (openedId === null) return;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setMotion(SLIDE_START);
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setMotion(SLIDE_END));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
      // Restoring the prior id keeps a cancelled run replayable, so a
      // strict-mode double invocation still finishes at the resting state.
      shownId.current = previous;
    };
  }, [openedId]);

  return <section ref={props.paneRef} className={`work-detail-pane ${opened ? "is-open" : ""}`} aria-label="Work context" aria-live="polite">
    {opened
      ? <div className="detail-scroll" ref={scrollRef}>
        <div className="detail-motion" style={motion}>
          <OpenedPane opened={opened} props={props} />
        </div>
      </div>
      : <EmptyPane />}
  </section>;
}
