"use client";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { decodeReportDetail, decodeReportProvenancePage } from "@/lib/job-completion-reports";
import type { JobReportDetail, JobReportFollowUp, JobReportProvenancePage } from "@/lib/types";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import JobReportContent from "@/components/job-report-content";
import { compareUtcDateTimes } from "@/lib/wire-guards";

function followsItem(item: JobReportFollowUp, previous: JobReportFollowUp): boolean {
  const timeOrder = compareUtcDateTimes(item.created_at, previous.created_at);
  return timeOrder > 0
    || timeOrder === 0 && item.id.toLowerCase() > previous.id.toLowerCase();
}

function ProvenanceDirection({ projectId, workItemId, direction, refreshSignal, onOpenWork }: {
  projectId: string; workItemId: string; direction: "origin" | "created";
  refreshSignal: number;
  onOpenWork: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
}) {
  const [page, setPage] = useState<JobReportProvenancePage | null>(null);
  const [items, setItems] = useState<JobReportFollowUp[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [report, setReport] = useState<JobReportDetail | null>(null);
  const [selectedReport, setSelectedReport] = useState<{
    id: string;
    projectId: string;
  } | null>(null);
  const [reload, setReload] = useState(0);
  const [reportError, setReportError] = useState("");
  const [reportLoading, setReportLoading] = useState(false);
  const [reportReload, setReportReload] = useState(0);
  useEffect(() => { setCursor(null); setItems([]); setPage(null); }, [projectId, workItemId, refreshSignal]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    const params = new URLSearchParams({ direction, limit: "20" });
    if (cursor) params.set("cursor", cursor);
    api<unknown>(`/projects/${projectId}/work-items/${workItemId}/report-follow-ups?${params}`, { signal: controller.signal })
      .then((raw) => {
        const next = decodeReportProvenancePage(raw, projectId, { workItemId, direction });
        if (cursor) {
          const previous = items.at(-1);
          if (
            !page
            || next.as_of_sequence !== page.as_of_sequence
            || previous && next.items.some((item) => !followsItem(item, previous))
            || next.items.some((item) => items.some(
              (existing) => existing.id.toLowerCase() === item.id.toLowerCase()
            ))
          ) {
            throw new Error("Mnemonic returned incoherent report provenance.");
          }
        }
        if (controller.signal.aborted) return;
        setPage(next); setItems((current) => cursor ? [...current, ...next.items] : next.items);
      }).catch((failure) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, workItemId, direction, cursor, refreshSignal, reload]);
  useEffect(() => { setReport(null); setReportError(""); }, [projectId, selectedReport]);
  useEffect(() => {
    if (!selectedReport) { setReportLoading(false); return; }
    const controller = new AbortController();
    setReportLoading(true); setReportError("");
    api<unknown>(`/projects/${selectedReport.projectId}/job-completion-reports/${selectedReport.id}`, { signal: controller.signal })
      .then((raw) => { if (!controller.signal.aborted) setReport(decodeReportDetail(raw, selectedReport.projectId, selectedReport.id)); })
      .catch((failure) => { if (!controller.signal.aborted) setReportError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setReportLoading(false); });
    return () => controller.abort();
  }, [selectedReport, refreshSignal, reportReload]);
  useFailedReadRetry({ scope: `provenance:${projectId}:${workItemId}:${direction}:${cursor}`, failed: Boolean(error), busy: loading, retry: () => setReload((value) => value + 1) });
  useFailedReadRetry({ scope: `originating-report:${selectedReport?.projectId}:${selectedReport?.id}`, failed: Boolean(reportError), busy: reportLoading, enabled: Boolean(selectedReport), retry: () => setReportReload((value) => value + 1) });
  return <section>
    <h4>{direction === "origin" ? "Origin of this follow-up" : "Follow-ups from this work’s reports"}</h4>
    {loading && <p role="status">Loading report links…</p>}
    {error && <div className="error-notice" role="alert"><p>{error}</p><button type="button" onClick={() => setReload((value) => value + 1)}>Retry report links</button></div>}
    {!loading && !error && !items.length && <p className="field-hint">No report follow-up links.</p>}
    {items.length > 0 && <ul>{items.map((link) => <li key={link.id}>
      <button type="button" className="button button-secondary" onClick={() => setSelectedReport({ id: link.report_id, projectId: link.project_id })}>Read originating report</button>
      <p>Report <span className="mono">{link.report_id}</span></p>
      <p>Exact source work <span className="mono">{link.source_work_item_id}</span> <button type="button" className="button button-secondary" onClick={() => void onOpenWork(link.source_work_item_id, link.project_id)}>Open original work</button></p>
      <p>Follow-up work <span className="mono">{link.follow_up_work_item_id}</span> <button type="button" className="button button-secondary" onClick={() => void onOpenWork(link.follow_up_work_item_id, link.project_id)}>Open follow-up work</button></p>
    </li>)}</ul>}
    {page?.next_cursor && <button type="button" className="button button-secondary" disabled={loading} onClick={() => setCursor(page.next_cursor)}>Load more report links</button>}
    {reportLoading && <p role="status">Refreshing originating report…</p>}
    {reportError && <div className="error-notice" role="alert"><p>{reportError}</p><button type="button" onClick={() => setReportReload((value) => value + 1)}>Retry originating report</button></div>}
    {report && <aside className="job-report-card"><p className="section-label">Stored originating report{report.human_dismissed ? " · Human dismissed" : ""}</p><JobReportContent item={report} /><button type="button" className="button button-secondary" onClick={() => setSelectedReport(null)}>Close stored report</button></aside>}
  </section>;
}
export default function WorkReportProvenance(props: {
  projectId: string; workItemId: string; refreshSignal: number;
  onOpenWork: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
}) {
  return <section className="work-report-provenance"><h3>Job report follow-up links</h3>
    <p className="field-hint">These links retain the exact original report and work identities, including merged or deleted work. An unavailable work detail does not erase its provenance.</p>
    <ProvenanceDirection key={`${props.workItemId}:origin`} {...props} direction="origin" />
    <ProvenanceDirection key={`${props.workItemId}:created`} {...props} direction="created" />
  </section>;
}
