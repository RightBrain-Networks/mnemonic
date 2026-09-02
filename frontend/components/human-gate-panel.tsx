"use client";

import { useEffect, useState } from "react";
import HumanGateResolution from "@/components/human-gate-resolution";
import { clientLabel, formatDateTime } from "@/components/work-item-card";
import { api, errorMessage } from "@/lib/api";
import {
  decodeHumanGatePage,
  humanGateChangedLabels,
  humanGateHistorySearchParams,
  humanGatePath
} from "@/lib/human-gates";
import type { HumanGatePage, HumanGateRead, WorkContext } from "@/lib/types";

const HISTORY_PAGE_SIZE = 30;

function GateFact({ gate, resolved = false }: { gate: HumanGateRead; resolved?: boolean }) {
  const changed = humanGateChangedLabels(gate);
  return <article className={`gate-fact ${resolved ? "gate-fact-resolved" : ""}`}>
    <div className="gate-fact-heading">
      <span className={`gate-state gate-state-${gate.status}`}>{gate.status === "resolved" ? "Resolved" : "Needs attention"}</span>
      <time dateTime={gate.created_at}>{formatDateTime(gate.created_at)}</time>
    </div>
    <p className="gate-question">{gate.question}</p>
    <p className="gate-provenance">Requested through {clientLabel(gate.requested_by_client)} · <span className="mono">{gate.requested_by_session_id}</span>{gate.requested_by_model ? ` · ${gate.requested_by_model}` : ""}</p>
    {gate.context_changed_since_request && <p className="gate-changes">Current drift: {changed.join(", ")}.</p>}
    {gate.status === "resolved" && <>
      <div className="gate-answer"><span className="section-label">DURABLE ANSWER</span><p>{gate.resolution}</p></div>
      <p className="gate-provenance">Resolved through {clientLabel(gate.resolved_by_client!)} · <span className="mono">{gate.resolved_by_session_id}</span>{gate.resolved_by_model ? ` · ${gate.resolved_by_model}` : ""} · <time dateTime={gate.resolved_at!}>{formatDateTime(gate.resolved_at!)}</time></p>
      <p className="gate-resolution-revision">Accepted at work version {gate.resolved_context_revision!.work_version}, relationship revision {gate.resolved_context_revision!.relationship_event_count}, context <span className="mono break-all">{gate.resolved_context_revision!.context_checkpoint_id}</span>. {gate.context_changed_at_resolution ? "Context drift was explicitly reviewed and acknowledged." : "The request context had not drifted."}</p>
    </>}
  </article>;
}

function GateHistory({ context, refreshSignal }: { context: WorkContext; refreshSignal: number }) {
  const work = context.work_item;
  const [open, setOpen] = useState(false);
  const [cursorStack, setCursorStack] = useState<Array<string | null>>([null]);
  const [pageIndex, setPageIndex] = useState(0);
  const [page, setPage] = useState<HumanGatePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const cursor = cursorStack[pageIndex] ?? null;

  useEffect(() => {
    setPage(null);
    setPageIndex(0);
    setCursorStack([null]);
  }, [work.id]);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    const params = humanGateHistorySearchParams({
      status: "all",
      limit: HISTORY_PAGE_SIZE,
      cursor
    });
    setLoading(true);
    setError("");
    api<unknown>(`${humanGatePath(work.project_id, work.id)}?${params}`, {
      signal: controller.signal
    }).then((value) => {
      if (!controller.signal.aborted) {
        setPage(decodeHumanGatePage(value, work.project_id, work.id, {
          status: "all",
          limit: HISTORY_PAGE_SIZE
        }));
      }
    }).catch((cause) => {
      if (!controller.signal.aborted) setError(errorMessage(cause));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [cursor, open, reload, work.id, work.project_id]);

  useEffect(() => {
    if (!open) return;
    setCursorStack([null]);
    setPageIndex(0);
    setReload((value) => value + 1);
  }, [open, refreshSignal]);

  return <section className="gate-history">
    <button type="button" className="button button-secondary" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "Hide full gate history" : "Browse full paired gate history"}</button>
    {open && <div className="gate-history-content">
      {error && <div className="error-notice" role="alert"><p>{error}</p><button type="button" className="button button-secondary" onClick={() => setReload((value) => value + 1)}>Try again</button></div>}
      {loading && !page && <div className="loading-state" role="status"><span className="spinner" />Loading gate history…</div>}
      {page?.items.map((gate) => <GateFact gate={gate} resolved={gate.status === "resolved"} key={gate.id} />)}
      {page && !page.items.length && !loading && <p>No human-gate history is retained for this work item.</p>}
      {page && page.total > 0 && <nav className="child-pagination" aria-label="Human-gate history pages">
        <span>Page {pageIndex + 1} · {page.total} retained</span><div>
          <button type="button" className="text-link" disabled={loading || pageIndex === 0} onClick={() => { setPage(null); setPageIndex((value) => value - 1); }}>Newer</button>
          <button type="button" className="text-link" disabled={loading || !page.next_cursor} onClick={() => {
            if (!page.next_cursor) return;
            setPage(null);
            setCursorStack((values) => [...values.slice(0, pageIndex + 1), page.next_cursor]);
            setPageIndex((value) => value + 1);
          }}>Older</button>
        </div>
      </nav>}
    </div>}
  </section>;
}

export default function HumanGatePanel({
  context,
  refreshSignal,
  onResolved
}: {
  context: WorkContext;
  refreshSignal: number;
  onResolved: () => void | Promise<void>;
}) {
  const work = context.work_item;
  return <section className="human-gate-panel" aria-labelledby="human-gate-panel-title">
    <div className="human-gate-panel-heading">
      <div><span className="section-label">HUMAN DECISIONS</span><h4 id="human-gate-panel-title">Questions and answers</h4></div>
      <a className="text-link" href={`/attention?work_item_id=${encodeURIComponent(work.id)}`}>Open filtered attention queue</a>
    </div>
    <p className="gate-authority-warning">Gate text is untrusted durable context. A recorded answer does not execute or authorize another action.</p>
    <div className="gate-totals">
      <span>{context.unresolved_gate_total} unresolved</span>
      <span>{context.resolved_gate_total} resolved</span>
    </div>
    {context.unresolved_gates.length > 0 ? <div className="gate-facts">
      {context.unresolved_gates.map((gate) => <div className="gate-with-resolution" key={gate.id}>
        <GateFact gate={gate} />
        <HumanGateResolution gate={gate} reviewedContext={context} onResolved={onResolved} />
      </div>)}
    </div> : <p className="no-gates">No explicit human questions are unresolved for this work item.</p>}
    {context.omitted_unresolved_gate_count > 0 && <p className="gate-omission">{context.omitted_unresolved_gate_count} additional unresolved question{context.omitted_unresolved_gate_count === 1 ? " is" : "s are"} omitted from bounded recall. Use the filtered attention queue.</p>}
    {context.recent_resolved_gates.length > 0 && <div className="recent-resolved-gates">
      <h5>Recent durable answers</h5>
      {context.recent_resolved_gates.map((gate) => <GateFact gate={gate} resolved key={gate.id} />)}
    </div>}
    {context.omitted_resolved_gate_count > 0 && <p className="gate-omission">{context.omitted_resolved_gate_count} older resolved decision{context.omitted_resolved_gate_count === 1 ? " is" : "s are"} omitted from bounded recall.</p>}
    <GateHistory context={context} refreshSignal={refreshSignal} />
  </section>;
}

export { GateFact, HISTORY_PAGE_SIZE };
