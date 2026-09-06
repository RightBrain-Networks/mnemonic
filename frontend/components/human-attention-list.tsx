"use client";

import ExternalReferences from "@/components/external-references";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useEffect, useRef, useState } from "react";
import HumanGateResolution from "@/components/human-gate-resolution";
import MarkdownContent from "@/components/markdown-content";
import { SearchBreadcrumb } from "@/components/work-hierarchy";
import {
  OperationalBadge,
  StatusBadge,
  clientLabel,
  formatDateTime
} from "@/components/work-item-card";
import { api, errorMessage } from "@/lib/api";
import {
  decodeHumanAttentionPage,
  humanAttentionSearchParams
} from "@/lib/human-gates";
import type { HumanAttentionPage, Project, WorkSummary } from "@/lib/types";
import { validUuid } from "@/lib/wire-guards";

const ATTENTION_PAGE_SIZE = 30;

function requestedThrough(item: HumanAttentionPage["items"][number]): string {
  const gate = item.gate;
  return [
    clientLabel(gate.requested_by_client),
    gate.requested_by_session_id,
    gate.requested_by_model
  ].filter(Boolean).join(" · ");
}

function locationWorkFilter(): string | undefined {
  if (typeof window === "undefined") return undefined;
  const value = new URLSearchParams(window.location.search).get("work_item_id");
  return value && validUuid(value) ? value : undefined;
}

export default function HumanAttentionList({
  project,
  refreshSignal,
  onOpen,
  onResolved
}: {
  project: Project | undefined;
  refreshSignal: number;
  onOpen: (summary: WorkSummary) => void;
  onResolved: () => void;
}) {
  const [workItemId, setWorkItemId] = useState<string | undefined>();
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<HumanAttentionPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reload, setReload] = useState(0);
  const [resolutionStatus, setResolutionStatus] = useState("");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const pendingResolvedGateId = useRef<string | null>(null);
  const cursor = cursorStack[pageIndex] ?? null;

  useEffect(() => {
    setWorkItemId(locationWorkFilter());
  }, []);

  useEffect(() => {
    setCursorStack([null]);
    setPageIndex(0);
    setPage(null);
    setLoadError("");
    setResolutionStatus("");
    pendingResolvedGateId.current = null;
  }, [project?.id, workItemId]);

  useEffect(() => {
    if (!project) {
      setPage(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    const params = humanAttentionSearchParams({
      workItemId,
      limit: ATTENTION_PAGE_SIZE,
      cursor
    });
    setLoading(true);
    setLoadError("");
    api<unknown>(`/projects/${encodeURIComponent(project.id)}/human-attention?${params}`, {
      signal: controller.signal
    }).then((value) => {
      if (controller.signal.aborted) return;
      const nextPage = decodeHumanAttentionPage(value, project.id, {
        workItemId,
        limit: ATTENTION_PAGE_SIZE
      });
      if (!nextPage.items.length && nextPage.total > 0 && pageIndex > 0) {
        setPageIndex((value) => Math.max(0, value - 1));
        return;
      }
      setPage(nextPage);
      const resolvedGateId = pendingResolvedGateId.current;
      if (
        resolvedGateId
        && !nextPage.items.some((item) => item.gate.id === resolvedGateId)
      ) {
        pendingResolvedGateId.current = null;
        setResolutionStatus(nextPage.total === 0
          ? "Answer recorded. No unresolved questions remain."
          : `Answer recorded. ${nextPage.total} unresolved question${nextPage.total === 1 ? " remains" : "s remain"}.`);
        titleRef.current?.focus();
      }
    }).catch((cause) => {
      if (!controller.signal.aborted) setLoadError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [cursor, pageIndex, project?.id, refreshSignal, reload, workItemId]);

  useFailedReadRetry({ scope: `attention:${project?.id}:${workItemId}:${cursor}`, failed: Boolean(loadError), busy: loading, enabled: Boolean(project), retry: () => setReload((value) => value + 1) });

  function refreshHead(): void {
    setCursorStack([null]);
    setPageIndex(0);
    setPage(null);
    setResolutionStatus("");
    pendingResolvedGateId.current = null;
    setReload((value) => value + 1);
  }

  function resolved(gateId: string): void {
    pendingResolvedGateId.current = gateId;
    setReload((value) => value + 1);
    onResolved();
  }

  if (!project) {
    return <section className="empty-state"><h2>Choose a project.</h2><p>Human questions are scoped to one project.</p></section>;
  }

  return <section className="attention-list" aria-labelledby="attention-list-title">
    <div className="attention-list-heading">
      <div><span className="section-label">EXPLICIT HUMAN QUESTIONS</span><h2 ref={titleRef} id="attention-list-title" tabIndex={-1}>{page ? `${page.total} waiting` : "Needs attention"}</h2></div>
      <button type="button" className="button button-secondary" disabled={loading} onClick={refreshHead}>Refresh queue</button>
    </div>
    {resolutionStatus && <p className="attention-resolution-status" role="status" aria-live="polite">{resolutionStatus}</p>}
    {workItemId && <div className="attention-filter" role="status">
      <span>Filtered to work item <span className="mono">{workItemId}</span></span>
      <a href="/attention" className="text-link">Show every question</a>
    </div>}
    <p className="attention-authority-note">This queue contains only explicit durable questions. Recording an answer executes nothing and is not authenticated approval.</p>
    {loadError && <div className="error-notice" role="alert"><p>{loadError}</p><button type="button" className="button button-secondary" onClick={() => setReload((value) => value + 1)}>Try again</button></div>}
    {loading && !page && <div className="loading-state" role="status"><span className="spinner" />Loading explicit questions…</div>}
    {!loading && page && !page.items.length && !loadError && <section className="empty-state attention-empty"><h2>No explicit human questions are waiting.</h2><p>This does not mean that every work item is ready; lifecycle holds, blockers, and active leases are separate facts.</p></section>}
    {page?.items.length ? <div className="attention-items" aria-busy={loading}>{page.items.map((item) => <article className="attention-card" key={item.gate.id}>
      <SearchBreadcrumb summary={item.summary} />
      <div className="attention-card-heading">
        <div><StatusBadge status={item.summary.work_item.status} readiness={item.summary.readiness} /><OperationalBadge readiness={item.summary.readiness} /></div>
        <span>Priority {item.summary.work_item.priority}</span>
      </div>
      <h3>{item.summary.work_item.title}</h3>
      <ExternalReferences references={item.summary.work_item.external_references} />
      <MarkdownContent className="attention-question">{item.gate.question}</MarkdownContent>
      <dl className="attention-provenance">
        <div><dt>Requested through</dt><dd>{requestedThrough(item)}</dd></div>
        <div><dt>Requested</dt><dd><time dateTime={item.gate.created_at}>{formatDateTime(item.gate.created_at)}</time></dd></div>
      </dl>
      <button type="button" className="button button-secondary" onClick={() => onOpen(item.summary)}>Open work context</button>
      <HumanGateResolution gate={item.gate} onResolved={() => resolved(item.gate.id)} />
    </article>)}</div> : null}
    {page && page.total > 0 && <nav className="pagination attention-pagination" aria-label="Human attention pages">
      <span>Page {pageIndex + 1} · {page.items.length} shown · {page.total} currently unresolved</span>
      <div>
        <button type="button" className="button button-secondary" disabled={loading || pageIndex === 0} onClick={() => {
          setPage(null);
          setPageIndex((value) => Math.max(0, value - 1));
        }}>Previous</button>
        <button type="button" className="button button-secondary" disabled={loading || !page.next_cursor} onClick={() => {
          if (!page.next_cursor) return;
          setPage(null);
          setCursorStack((values) => [...values.slice(0, pageIndex + 1), page.next_cursor]);
          setPageIndex((value) => value + 1);
        }}>Next</button>
      </div>
    </nav>}
  </section>;
}

export { ATTENTION_PAGE_SIZE };
