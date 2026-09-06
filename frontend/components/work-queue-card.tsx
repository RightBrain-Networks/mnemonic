"use client";

import ExternalReferences from "@/components/external-references";
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
import { discoveryLabel, hierarchyBranchTotals } from "@/lib/hierarchy-presentation";
import type { HierarchyPresentation, WorkSummary } from "@/lib/types";

export type QueueOptions = {
  selectedId: string | null;
  copiedKey: string | null;
  onSelect: (summary: WorkSummary) => void;
  onCopyPointer: (summary: WorkSummary) => void;
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
  const { selectedId, copiedKey, onSelect, onCopyPointer, register, unregister } = useQueueOptions();
  const work = summary.work_item;
  const context = summary.current_context;
  const id = work.id;
  const titleId = useId();
  const selected = selectedId === id;
  const copied = copiedKey === `${id}:pointer`;
  const descendants = presentation?.descendant_count ?? 0;
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
    <ExternalReferences references={work.external_references} />
    <div className="queue-card-footer">
      {presentation && descendants > 0 && <span className="queue-chip" title={descendantChipTitle(presentation, depth)}>{descendants} descendant{descendants === 1 ? "" : "s"}</span>}
      {attention > 0 && <span className="queue-chip queue-chip-attention">{attention} needs attention</span>}
      <span className="queue-card-arrow" aria-hidden="true">→</span>
      <button
        type="button"
        className={`button queue-copy-button ${copied ? "is-copied" : ""}`}
        aria-label={`Copy recall pointer for ${work.title}`}
        onClick={copyPointer}
        onKeyDown={(event) => { if (isActivationKey(event)) event.stopPropagation(); }}
      ><CopyIcon />{copied ? "Copied" : "Copy recall pointer"}</button>
    </div>
  </article>;
}
