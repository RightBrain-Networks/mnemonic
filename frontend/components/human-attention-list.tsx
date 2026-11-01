"use client";

import Link from "@/components/dashboard-link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ATTENTION_PAGE_SIZE, humanAttentionOptions, secondaryKeys } from "@/lib/secondary-queries";
import { useLocation } from "@tanstack/react-router";

import { useWorkItemMotion } from "@/components/use-work-item-motion";
import ExternalReferences from "@/components/external-references";
import CopyableId from "@/components/copyable-id";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useEffect, useRef, useState } from "react";
import HumanGateQuestion from "@/components/human-gate-question";
import { SearchBreadcrumb } from "@/components/work-hierarchy";
import {
  OperationalBadge,
  StatusBadge,
  clientLabel,
  formatDateTime
} from "@/components/work-item-card";
import { errorMessage } from "@/lib/api";
import type { HumanAttentionPage, Project, WorkSummary } from "@/lib/types";
import { validUuid } from "@/lib/wire-guards";

function requestedThrough(item: HumanAttentionPage["items"][number]): string {
  const gate = item.gate;
  return [
    clientLabel(gate.requested_by_client),
    gate.requested_by_session_id,
    gate.requested_by_model
  ].filter(Boolean).join(" · ");
}

function locationWorkFilter(search: string): string | undefined {
  const value = new URLSearchParams(search).get("work_item_id");
  return value && validUuid(value) ? value : undefined;
}

type AttentionListProps = {
  project: Project | undefined;
  copiedKey: string | null;
  onCopy: (value: string, key: string, success: string) => void;
  onOpen: (summary: WorkSummary) => void;
  onResolved: () => void;
};

export default function HumanAttentionList(props: AttentionListProps) {
  const query = useLocation({ select: (location) => location.searchStr });
  const workItemId = locationWorkFilter(query);
  // A scope change creates fresh navigation state before any read can observe
  // the previous scope's continuation token or cached cursor page.
  return <AttentionScope key={`${props.project?.id ?? "none"}:${workItemId ?? "all"}`} {...props} workItemId={workItemId} />;
}

function AttentionScope({ project, copiedKey, onCopy, onOpen, onResolved, workItemId }: AttentionListProps & { workItemId?: string }) {
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const client = useQueryClient();
  const [resolutionStatus, setResolutionStatus] = useState("");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const pendingResolvedGateId = useRef<string | null>(null);
  const cursor = cursorStack[pageIndex] ?? null;
  const result = useQuery({ ...humanAttentionOptions(project?.id ?? "", workItemId, cursor), enabled: Boolean(project) });
  const page = project ? result.data ?? null : null;
  const loading = Boolean(project) && result.isFetching;
  const loadError = project && result.error ? errorMessage(result.error) : "";
  const reload = () => { if (project) void client.invalidateQueries({ queryKey: secondaryKeys.attention(project.id) }); };
  const motionRef = useWorkItemMotion<HTMLDivElement>({
    itemIds: page?.items.map((item) => item.gate.id) ?? [],
    total: page?.total ?? null,
    viewKey: `${project?.id}:${workItemId}:${cursor}`,
    revision: page,
    snapshotSignal: result.isFetching,
    animateReplacements: true
  });

  useEffect(() => {
    if (!page || result.isFetching || result.error) return;
    if (!page.items.length && page.total > 0 && pageIndex > 0) {
      setPageIndex((value) => Math.max(0, value - 1));
      return;
    }
    const resolvedGateId = pendingResolvedGateId.current;
    if (resolvedGateId && !page.items.some((item) => item.gate.id === resolvedGateId)) {
      pendingResolvedGateId.current = null;
      setResolutionStatus(page.total === 0
        ? "Answer recorded. No unresolved questions remain."
        : `Answer recorded. ${page.total} unresolved question${page.total === 1 ? " remains" : "s remain"}.`);
      titleRef.current?.focus();
    }
  }, [page, pageIndex, result.isFetching, result.error]);

  useFailedReadRetry({ scope: `attention:${project?.id}:${workItemId}:${cursor}`, failed: Boolean(loadError), busy: loading, enabled: Boolean(project), retry: reload });

  function refreshHead(): void {
    setCursorStack([null]);
    setPageIndex(0);
    setResolutionStatus("");
    pendingResolvedGateId.current = null;
    reload();
  }

  function resolved(gateId: string): void {
    pendingResolvedGateId.current = gateId;
    reload();
    onResolved();
  }

  if (!project) {
    return <section className="empty-state"><h2>Choose a project.</h2><p>Product Decision Records are scoped to one project.</p></section>;
  }

  return <section className="attention-list" aria-labelledby="attention-list-title">
    <div className="attention-list-heading">
      <div><span className="section-label">PRODUCT DECISION RECORDS</span><h2 ref={titleRef} id="attention-list-title" tabIndex={-1}>{page ? `${page.total} waiting` : "Needs attention"}</h2></div>
      <button type="button" className="button button-secondary" disabled={loading} onClick={refreshHead}>Refresh queue</button>
    </div>
    {resolutionStatus && <p className="attention-resolution-status" role="status" aria-live="polite">{resolutionStatus}</p>}
    {workItemId && <div className="attention-filter" role="status">
      <span>Filtered to work item <span className="mono">{workItemId}</span></span>
      <Link href={`/attention?project=${project.id}`} className="text-link">Show every question</Link>
    </div>}
    <p className="attention-authority-note">Each question is a Product Decision Record. Read the current question and record your answer; both remain in its immutable history. Open the PDR to view related decisions, work, and code references.</p>
    {loadError && <div className="error-notice" role="alert"><p>{loadError}</p><button type="button" className="button button-secondary" onClick={() => reload()}>Try again</button></div>}
    {loading && !page && <div className="loading-state" role="status"><span className="spinner" />Loading explicit questions…</div>}
    {page && !page.items.length && !loadError && <section className="empty-state attention-empty"><h2>No explicit human questions are waiting.</h2><p>This does not mean that every work item is ready; lifecycle holds, blockers, and active leases are separate facts.</p></section>}
    <div className="attention-items" ref={motionRef} aria-busy={loading}>{page?.items.map((item) => <article className="attention-card" key={item.gate.id} data-work-item-id={item.gate.id}>
      <SearchBreadcrumb summary={item.summary} />
      <div className="attention-card-heading">
        <div><StatusBadge status={item.summary.work_item.status} readiness={item.summary.readiness} /><OperationalBadge readiness={item.summary.readiness} /></div>
        <span>Priority {item.summary.work_item.priority}</span>
      </div>
      <h3>{item.gate.decision?.metadata.title ?? item.summary.work_item.title}</h3>
      <ExternalReferences references={item.summary.work_item.external_references} />
      <HumanGateQuestion gate={item.gate} onResolved={() => resolved(item.gate.id)} onRefresh={() => reload()} />
      <dl className="attention-provenance">
        <div><dt>Work item</dt><dd>{item.summary.work_item.title}</dd></div>
        <div><dt>Current question by</dt><dd>{requestedThrough(item)}</dd></div>
        <div><dt>First requested</dt><dd><time dateTime={item.gate.created_at}>{formatDateTime(item.gate.created_at)}</time></dd></div>
      </dl>
      <footer className="attention-card-footer">
        <button type="button" className="button button-secondary" onClick={() => onOpen(item.summary)}>Open work context</button>
        <CopyableId value={item.gate.id} label="Copy question ID" copied={copiedKey === `question:${item.gate.id}`}
          onCopy={() => onCopy(item.gate.id, `question:${item.gate.id}`, `Question ID copied: ${item.gate.id}`)} />
      </footer>
    </article>)}</div>
    {page && page.total > 0 && <nav className="pagination attention-pagination" aria-label="Human attention pages">
      <span>Page {pageIndex + 1} · {page.items.length} shown · {page.total} currently unresolved</span>
      <div>
        <button type="button" className="button button-secondary" disabled={loading || pageIndex === 0} onClick={() => {
          setPageIndex((value) => Math.max(0, value - 1));
        }}>Previous</button>
        <button type="button" className="button button-secondary" disabled={loading || !page.next_cursor} onClick={() => {
          if (!page.next_cursor) return;
          setCursorStack((values) => [...values.slice(0, pageIndex + 1), page.next_cursor]);
          setPageIndex((value) => value + 1);
        }}>Next</button>
      </div>
    </nav>}
  </section>;
}

export { ATTENTION_PAGE_SIZE };
