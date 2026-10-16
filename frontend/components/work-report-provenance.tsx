"use client";
import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { workReportDetailOptions, workReportProvenanceOptions } from "@/lib/work-report-queries";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import JobReportContent from "@/components/job-report-content";

function ProvenanceDirection({ projectId, workItemId, direction, onOpenWork }: {
  projectId: string; workItemId: string; direction: "origin" | "created";
  refreshSignal: number;
  onOpenWork: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
}) {
  const provenance = useInfiniteQuery(workReportProvenanceOptions(projectId, workItemId, direction));
  const items = provenance.data?.pages.flatMap((page) => page.items) ?? [];
  const error = provenance.error ? errorMessage(provenance.error) : "";
  const loading = provenance.isFetching;
  const [selectedReport, setSelectedReport] = useState<{
    id: string;
    projectId: string;
  } | null>(null);
  const reportRead = useQuery(workReportDetailOptions(projectId, workItemId, selectedReport));
  const report = reportRead.data;
  const reportError = reportRead.error ? errorMessage(reportRead.error) : "";
  const reportLoading = reportRead.isFetching;
  const retryProvenance = () => {
    if (provenance.isFetchNextPageError) void provenance.fetchNextPage({ cancelRefetch: false });
    else void provenance.refetch();
  };
  useFailedReadRetry({ scope: `provenance:${projectId}:${workItemId}:${direction}`, failed: provenance.isError, busy: loading, retry: retryProvenance });
  useFailedReadRetry({ scope: `originating-report:${selectedReport?.projectId}:${selectedReport?.id}`, failed: reportRead.isError, busy: reportLoading, enabled: Boolean(selectedReport), retry: () => { void reportRead.refetch(); } });
  return <section>
    <h4>{direction === "origin" ? "Origin of this follow-up" : "Follow-ups from this work’s reports"}</h4>
    {loading && <p role="status">Loading report links…</p>}
    {error && <div className="error-notice" role="alert"><p>{error}</p><button type="button" onClick={retryProvenance}>Retry report links</button></div>}
    {!loading && !error && !items.length && <p className="field-hint">No report follow-up links.</p>}
    {items.length > 0 && <ul>{items.map((link) => <li key={link.id}>
      <button type="button" className="button button-secondary" onClick={() => {
        if (selectedReport?.id === link.report_id && selectedReport.projectId === link.project_id) void reportRead.refetch();
        else setSelectedReport({ id: link.report_id, projectId: link.project_id });
      }}>Read originating report</button>
      <p>Report <span className="mono">{link.report_id}</span></p>
      <p>Exact source work <span className="mono">{link.source_work_item_id}</span> <button type="button" className="button button-secondary" onClick={() => void onOpenWork(link.source_work_item_id, link.project_id)}>Open original work</button></p>
      <p>Follow-up work <span className="mono">{link.follow_up_work_item_id}</span> <button type="button" className="button button-secondary" onClick={() => void onOpenWork(link.follow_up_work_item_id, link.project_id)}>Open follow-up work</button></p>
    </li>)}</ul>}
    {provenance.hasNextPage && <button type="button" className="button button-secondary" disabled={loading} onClick={() => void provenance.fetchNextPage({ cancelRefetch: false })}>Load more report links</button>}
    {reportLoading && <p role="status">Refreshing originating report…</p>}
    {reportError && <div className="error-notice" role="alert"><p>{reportError}</p><button type="button" onClick={() => void reportRead.refetch()}>Retry originating report</button></div>}
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
