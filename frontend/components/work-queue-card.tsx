"use client";

import {
  createContext,
  useContext,
  useEffect,
  useId,
  type KeyboardEvent,
  type MouseEvent
} from "react";
import {
  OperationalBadge,
  StatusBadge,
  clientLabel,
  formatDateTime
} from "@/components/work-item-card";
import { StatusActionButton } from "@/components/work-detail-pane";
import { discoveryLabel, hierarchyBranchTotals } from "@/lib/hierarchy-presentation";
import type { HierarchyPresentation, Project, WorkSummary } from "@/lib/types";
import type { ManualStatusAction } from "@/lib/work-status-actions";

export type QueueOptions = {
  selectedId: string | null;
  copiedKey: string | null;
  projects: readonly Project[];
  statusChangingId: string | null;
  movingId: string | null;
  reportSettingsProjectId: string | null;
  isMutationBlocked: (summary: WorkSummary) => boolean;
  onSelect: (summary: WorkSummary) => void;
  onCopyPointer: (summary: WorkSummary) => void;
  onStatusAction: (action: ManualStatusAction, summary: WorkSummary) => void;
  onMove: (summary: WorkSummary, targetProjectId: string) => void;
  register: (id: string, summary: WorkSummary) => void;
  unregister: (id: string) => void;
};

export const QueueOptionsContext = createContext<QueueOptions | null>(null);

export function useQueueOptions(): QueueOptions {
  const options = useContext(QueueOptionsContext);
  if (!options) throw new Error("WorkQueueCard must render inside WorkQueue.");
  return options;
}

function CopyIcon() {
  return <svg width={12} height={12} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 5V3h12v14h-3M3 7h12v14H3V7Z" /></svg>;
}

function isActivationKey(event: KeyboardEvent<HTMLElement>): boolean {
  return event.key === "Enter" || event.key === " ";
}

export function descendantChipTitle(presentation: HierarchyPresentation, depth: number): string {
  const origin = discoveryLabel(presentation, depth);
  const totals = hierarchyBranchTotals(presentation).map((total) => total.label);
  return [...(origin ? [origin] : []), ...totals].join(" · ");
}

type Props = {
  summary: WorkSummary;
  presentation?: HierarchyPresentation;
  depth?: number;
};

export default function WorkQueueCard({ summary, presentation, depth = 0 }: Props) {
  const {
    selectedId,
    copiedKey,
    projects,
    statusChangingId,
    movingId,
    reportSettingsProjectId,
    isMutationBlocked,
    onSelect,
    onCopyPointer,
    onStatusAction,
    onMove,
    register,
    unregister
  } = useQueueOptions();
  const work = summary.work_item;
  const context = summary.current_context;
  const id = work.id;
  const titleId = useId();
  const selected = selectedId === id;
  const copied = copiedKey === `${id}:pointer`;
  const descendants = presentation?.descendant_count ?? 0;
  const mutationBlocked = isMutationBlocked(summary);
  const reviewLocked = summary.readiness.active_lease?.purpose === "code_review";
  const actionsDisabled = mutationBlocked || summary.readiness.is_duplicate || reviewLocked;
  const moveDisabledReason = mutationBlocked
    ? "Resolve the pending mutation before moving this work item."
    : reviewLocked
      ? "Work under code review must remain in its original project."
      : summary.readiness.has_active_lease
        ? "Release the active lease before moving this work item."
        : summary.readiness.is_duplicate
          ? "Duplicate audit records cannot be moved."
          : summary.readiness.is_gated
            ? "Resolve every human question before moving this work item."
            : null;
  const attention = presentation?.branch_unresolved_human_gate_count ?? 0;

  useEffect(() => {
    register(id, summary);
    return () => unregister(id);
  }, [id, register, summary, unregister]);

  const select = () => {
    if (!selected) onSelect(summary);
  };
  const copyPointer = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    onCopyPointer(summary);
  };

  return <article
    className={`work-item-card queue-card ${selected ? "is-selected" : ""}`}
    role="option"
    tabIndex={0}
    aria-selected={selected}
    aria-labelledby={titleId}
    data-queue-option={id}
    onClick={select}
    onKeyDown={(event) => {
      if (event.target !== event.currentTarget || !isActivationKey(event)) return;
      event.preventDefault();
      select();
    }}
  >
    <div className="queue-card-topline">
      <StatusBadge status={work.status} readiness={summary.readiness} />
      <OperationalBadge readiness={summary.readiness} />
      <span className="queue-card-meta">
        {clientLabel(context.source_client)}
        <span className="sep">·</span>
        Priority {work.priority}
        <span className="sep">·</span>
        <time dateTime={work.updated_at}>{formatDateTime(work.updated_at)}</time>
      </span>
    </div>
    <h2 className="queue-card-title" id={titleId}>{work.title}</h2>
    <p className="queue-card-summary">{work.summary}</p>
    <div className="queue-card-footer">
      {presentation && descendants > 0 && <span className="queue-chip" title={descendantChipTitle(presentation, depth)}>{descendants} descendant{descendants === 1 ? "" : "s"}</span>}
      {attention > 0 && <span className="queue-chip queue-chip-attention">{attention} needs attention</span>}
      <span className="queue-card-arrow" aria-hidden="true">→</span>
      <div className="queue-card-actions">
        <StatusActionButton
          summary={summary}
          projects={projects}
          disabled={actionsDisabled || Boolean(
            statusChangingId && statusChangingId !== id
            || movingId && movingId !== id
          )}
          busy={statusChangingId === id}
          reportSettingsReady={reportSettingsProjectId === work.project_id}
          moveDisabled={Boolean(moveDisabledReason)}
          moving={movingId === id}
          moveTitle={moveDisabledReason ?? "Move this work item to another project"}
          onAction={onStatusAction}
          onMove={(targetProjectId) => onMove(summary, targetProjectId)}
          compact
        />
        <button
          type="button"
          className={`button queue-copy-button ${copied ? "is-copied" : ""}`}
          aria-label={`Copy recall pointer for ${work.title}`}
          onClick={copyPointer}
          onKeyDown={(event) => { if (isActivationKey(event)) event.stopPropagation(); }}
        ><CopyIcon />{copied ? "Copied" : "Copy recall pointer"}</button>
      </div>
    </div>
  </article>;
}
