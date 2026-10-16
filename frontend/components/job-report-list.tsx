"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { jobReportListOptions, secondaryKeys } from "@/lib/secondary-queries";
import { workReportDetailOptions } from "@/lib/work-report-queries";
import { errorMessage } from "@/lib/api";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { mutationReportKey, useMutationIntentRegistry, useMutationIntents } from "@/lib/mutation-intent";
import type { JobReportEnvelope } from "@/lib/types";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { useWorkItemMotion } from "@/components/use-work-item-motion";
import JobReportContent from "@/components/job-report-content";
import JobReportFollowUpForm from "@/components/job-report-follow-up-form";
import SummaryReviewButton from "@/components/summary-review-button";

export default function JobReportList({ projectId, onChanged, onOpenWork, repositoryUrl }: {
  repositoryUrl?: string | null;
  projectId: string; onChanged: () => void;
  onOpenWork: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
}) {
  const registry = useMutationIntentRegistry();
  useMutationIntents();
  const client = useQueryClient();
  const listOptions = jobReportListOptions(projectId);
  const listQuery = useInfiniteQuery(listOptions);
  const pages = listQuery.data?.pages;
  const page = pages?.at(-1);
  const items = useMemo(() => pages?.flatMap((page) => page.items) ?? [], [pages]);
  const loading = listQuery.isFetching;
  const error = listQuery.error ? errorMessage(listQuery.error) : "";
  const reload = () => void client.invalidateQueries({ queryKey: secondaryKeys.reports(projectId) });
  const [actionError, setActionError] = useState("");
  // Retain the chosen envelope as the form's initial context, even if it leaves
  // the undismissed list. The form itself owns its editable draft.
  const [selectedReport, setSelectedReport] = useState<JobReportEnvelope | null>(null);
  const contextQuery = useQuery(workReportDetailOptions(projectId, selectedReport?.report.work_item_id ?? "",
    selectedReport ? { projectId, id: selectedReport.report.id } : null));
  const followUpReport = selectedReport ? contextQuery.data ?? selectedReport : null;
  const followUpContextError = selectedReport && contextQuery.error ? errorMessage(contextQuery.error) : "";
  const retryContext = () => { if (selectedReport) void contextQuery.refetch({ cancelRefetch: false }); };
  const [created, setCreated] = useState<{ reportId: string; workId: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [motionCapture, setMotionCapture] = useState(0);
  const motionRef = useWorkItemMotion<HTMLDivElement>({
    itemIds: items.map((item) => item.report.id),
    total: page ? items.length : null,
    viewKey: `${projectId}:${pages?.length ?? 0}`,
    revision: items,
    snapshotSignal: `${loading}:${motionCapture}`,
    animateReplacements: true,
    reboundOnRemoval: true
  });
  const heading = useRef<HTMLHeadingElement>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useFailedReadRetry({ scope: `reports:${projectId}`, failed: Boolean(error), busy: loading, retry: reload });
  useFailedReadRetry({ scope: `follow-up-report:${projectId}:${selectedReport?.report.id}`, failed: Boolean(followUpContextError), busy: contextQuery.isFetching, enabled: Boolean(selectedReport), retry: retryContext });
  useEffect(() => registry.subscribeRecovered((intent) => {
    if (intent.projectId === projectId && (intent.kind === "dismiss_job_completion_report" || intent.kind === "create_job_completion_report_follow_up")) {
      setSelectedReport((current) => current && intent.kind === "create_job_completion_report_follow_up"
        && intent.conflictKeys.includes(mutationReportKey(projectId, current.report.id))
        ? null : current);
      if (intent.kind === "dismiss_job_completion_report") setActionError("");
      onChanged();
    }
  }), [registry, projectId, onChanged]);
  async function dismiss(item: JobReportEnvelope) {
    const report = item.report;
    setMotionCapture((value) => value + 1);
    setActionError("");
    try {
      await registry.execute({
        kind: "dismiss_job_completion_report", slot: `dismiss-report:${projectId}:${report.id}`,
        projectId, conflictKeys: [mutationReportKey(projectId, report.id)], method: "POST",
        path: `/projects/${projectId}/job-completion-reports/${report.id}/dismiss`,
        payload: { actor: dashboardMutationActor(dashboardSessionId()) }
      });
      if (!alive.current) return;
      void client.cancelQueries({ queryKey: listOptions.queryKey, exact: true });
      client.setQueryData(listOptions.queryKey, (current) => current ? {
        ...current, pages: current.pages.map((entry) => ({ ...entry, items: entry.items.filter((value) => value.report.id !== report.id) }))
      } : current);
      setAnnouncement(`Summary for ${report.work_title_at_closeout} dismissed.`);
      heading.current?.focus({ preventScroll: true });
      reload();
      onChanged();
    } catch (failure) { if (alive.current) setActionError(errorMessage(failure)); }
  }
  return <section className="job-report-list" aria-labelledby="summary-list-title">
    <h2 id="summary-list-title" className="section-label" tabIndex={-1} ref={heading}>Reports to review</h2>
    <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => reload()}>Reload reports</button></div>}
    {actionError && <p className="error-notice" role="alert">{actionError}</p>}
    {loading && !page && <p className="loading-state" role="status"><span className="spinner" />Loading summaries…</p>}
    {page && !error && items.length === 0 && <div className="empty-state"><h3>You’re caught up.</h3><p>New closeout reports will appear here. Dismissed reports remain available through the API.</p></div>}
    {followUpReport && <aside className="job-report-card" aria-label="Follow-up draft and original report">
      <JobReportContent item={followUpReport} />
      {followUpContextError && <div className="error-notice" role="alert"><p>{followUpContextError}</p><button type="button" onClick={() => retryContext()}>Retry original report context</button></div>}
      <JobReportFollowUpForm key={followUpReport.report.id} item={followUpReport}
        onCancel={() => { setSelectedReport(null); heading.current?.focus(); }}
        onCreated={(result) => {
          const reportId = result.follow_up.report_id;
          setCreated({ reportId, workId: result.work_item.id }); setSelectedReport(null);
          reload();
          onChanged(); setAnnouncement("Follow-up created in Pending. The original report is still available for review.");
        }} />
    </aside>}
    <div className="job-report-items" ref={motionRef} aria-busy={loading}>{items.map((item) => {
      const blocked = registry.blocks([mutationReportKey(projectId, item.report.id)]);
      const formOpen = followUpReport?.report.id === item.report.id;
      return <article className="job-report-card" key={item.report.id} data-work-item-id={item.report.id} aria-label={`Report for ${item.report.work_title_at_closeout}`}>
        <JobReportContent item={item} />
        <div className="report-card-actions">
          <SummaryReviewButton item={item} repositoryUrl={repositoryUrl} onChanged={onChanged} />
          {!item.source_work_state.deleted && <button type="button" className="button button-secondary" onClick={() => void onOpenWork(item.report.work_item_id, item.report.project_id)}>Open original work</button>}
          <button type="button" className="button button-secondary" disabled={blocked || followUpReport !== null} onClick={() => { setSelectedReport(item); setCreated(null); }}>Create Follow-up</button>
          <button type="button" className="button button-primary" disabled={blocked || formOpen} onClick={() => void dismiss(item)}>Dismiss</button>
          {item.follow_up_count !== "0" && <span className="field-hint">{item.follow_up_count} follow-up{item.follow_up_count === "1" ? "" : "s"}</span>}
        </div>
        {created?.reportId === item.report.id && <div className="detail-notice" role="status"><p>Follow-up created in Pending.</p><button type="button" className="button button-secondary" onClick={() => void onOpenWork(created.workId, projectId)}>Open work</button></div>}

      </article>;
    })}</div>
    {listQuery.hasNextPage && <button type="button" className="button button-secondary" disabled={loading} onClick={() => void listQuery.fetchNextPage({ cancelRefetch: false })}>Load more summaries</button>}
  </section>;
}
