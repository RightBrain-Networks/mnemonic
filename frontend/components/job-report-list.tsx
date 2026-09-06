"use client";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { decodeReportDetail, decodeReportPage } from "@/lib/job-completion-reports";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { mutationReportKey, useMutationIntentRegistry, useMutationIntents } from "@/lib/mutation-intent";
import type { JobReportEnvelope, JobReportPage } from "@/lib/types";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import JobReportContent from "@/components/job-report-content";
import JobReportFollowUpForm from "@/components/job-report-follow-up-form";

export default function JobReportList({ projectId, refreshSignal, onChanged, onOpenWork }: {
  projectId: string; refreshSignal: number; onChanged: () => void;
  onOpenWork: (workItemId: string, preferredProjectId?: string) => void | Promise<void>;
}) {
  const registry = useMutationIntentRegistry();
  useMutationIntents();
  const [items, setItems] = useState<JobReportEnvelope[]>([]);
  const [page, setPage] = useState<JobReportPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [followUpContextError, setFollowUpContextError] = useState("");
  const [followUpContextLoading, setFollowUpContextLoading] = useState(false);
  const [followUpContextReload, setFollowUpContextReload] = useState(0);
  const [reload, setReload] = useState(0);
  const [followUpReport, setFollowUpReport] = useState<JobReportEnvelope | null>(null);
  const [created, setCreated] = useState<{ reportId: string; workId: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const dismissed = useRef(new Set<string>());
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; controller.current?.abort(); generation.current += 1; }; }, []);
  async function load(more = false) {
    const previous = more ? page : null;
    if (more && !previous?.next_cursor) return;
    const request = ++generation.current;
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ dismissal: "undismissed", limit: "20" });
      if (previous?.next_cursor) params.set("cursor", previous.next_cursor);
      const raw = await api<unknown>(`/projects/${projectId}/job-completion-reports?${params}`, { signal: abort.signal });
      const next = decodeReportPage(raw, projectId, { previous: previous ?? undefined });
      if (request !== generation.current || abort.signal.aborted) return;
      setPage(next);
      setItems((current) => {
        const candidates = more ? [...current, ...next.items] : next.items;
        const seen = new Set<string>();
        return candidates.filter((item) => {
          if (dismissed.current.has(item.report.id) || seen.has(item.report.id)) return false;
          seen.add(item.report.id); return true;
        });
      });
    } catch (failure) { if (request === generation.current && !abort.signal.aborted) setError(errorMessage(failure)); }
    finally { if (request === generation.current && !abort.signal.aborted) setLoading(false); }
  }
  useEffect(() => { void load(); }, [projectId, refreshSignal, reload]);
  useFailedReadRetry({ scope: `reports:${projectId}`, failed: Boolean(error), busy: loading, retry: () => setReload((value) => value + 1) });
  // Refresh the retained envelope by identity; the stable form key preserves human input.
  const followUpReportId = followUpReport?.report.id;
  useEffect(() => {
    setFollowUpContextError("");
    if (!followUpReportId) { setFollowUpContextLoading(false); return; }
    const abort = new AbortController();
    setFollowUpContextLoading(true);
    api<unknown>(`/projects/${projectId}/job-completion-reports/${followUpReportId}`, { signal: abort.signal })
      .then((raw) => {
        const next = decodeReportDetail(raw, projectId, followUpReportId);
        if (!abort.signal.aborted) setFollowUpReport((current) => current?.report.id === followUpReportId ? next : current);
      })
      .catch((failure) => { if (!abort.signal.aborted) setFollowUpContextError(errorMessage(failure)); })
      .finally(() => { if (!abort.signal.aborted) setFollowUpContextLoading(false); });
    return () => abort.abort();
  }, [projectId, followUpReportId, refreshSignal, followUpContextReload]);
  useFailedReadRetry({ scope: `follow-up-report:${projectId}:${followUpReportId}`, failed: Boolean(followUpContextError), busy: followUpContextLoading, enabled: Boolean(followUpReportId), retry: () => setFollowUpContextReload((value) => value + 1) });
  useEffect(() => registry.subscribeRecovered((intent) => {
    if (intent.projectId === projectId && (intent.kind === "dismiss_job_completion_report" || intent.kind === "create_job_completion_report_follow_up")) {
      setFollowUpReport((current) => current && intent.kind === "create_job_completion_report_follow_up"
        && intent.conflictKeys.includes(mutationReportKey(projectId, current.report.id))
        ? null : current);
      if (intent.kind === "dismiss_job_completion_report") setActionError("");
      setReload((value) => value + 1); onChanged();
    }
  }), [registry, projectId, onChanged]);
  async function dismiss(item: JobReportEnvelope) {
    const report = item.report;
    setActionError("");
    try {
      await registry.execute({
        kind: "dismiss_job_completion_report", slot: `dismiss-report:${projectId}:${report.id}`,
        projectId, conflictKeys: [mutationReportKey(projectId, report.id)], method: "POST",
        path: `/projects/${projectId}/job-completion-reports/${report.id}/dismiss`,
        payload: { actor: dashboardMutationActor(dashboardSessionId()) }
      });
      if (!alive.current) return;
      generation.current += 1;
      controller.current?.abort();
      dismissed.current.add(report.id);
      setItems((current) => current.filter((value) => value.report.id !== report.id));
      setAnnouncement(`Summary for ${report.work_title_at_closeout} dismissed.`);
      heading.current?.focus();
      setReload((value) => value + 1);
      onChanged();
    } catch (failure) { if (alive.current) setActionError(errorMessage(failure)); }
  }
  return <section className="job-report-list" aria-labelledby="summary-list-title">
    <h2 id="summary-list-title" className="section-label" tabIndex={-1} ref={heading}>Reports to review</h2>
    <p className="sr-only" role="status" aria-live="polite">{announcement}</p>
    {error && <div className="error-notice" role="alert"><p>{error}</p><button className="button button-secondary" onClick={() => setReload((value) => value + 1)}>Reload reports</button></div>}
    {actionError && <p className="error-notice" role="alert">{actionError}</p>}
    {loading && <p className="loading-state" role="status"><span className="spinner" />Loading summaries…</p>}
    {!loading && !error && items.length === 0 && <div className="empty-state"><h3>You’re caught up.</h3><p>New closeout reports will appear here. Dismissed reports remain available through the API.</p></div>}
    {followUpReport && <aside className="job-report-card" aria-label="Follow-up draft and original report">
      <JobReportContent item={followUpReport} />
      {followUpContextError && <div className="error-notice" role="alert"><p>{followUpContextError}</p><button type="button" onClick={() => setFollowUpContextReload((value) => value + 1)}>Retry original report context</button></div>}
      <JobReportFollowUpForm key={followUpReport.report.id} item={followUpReport}
        onCancel={() => { setFollowUpReport(null); heading.current?.focus(); }}
        onCreated={(result) => {
          const reportId = result.follow_up.report_id;
          setCreated({ reportId, workId: result.work_item.id }); setFollowUpReport(null);
          setItems((current) => current.map((entry) => entry.report.id === reportId
            ? { ...entry, follow_up_count: (BigInt(entry.follow_up_count) + 1n).toString() } : entry));
          onChanged(); setAnnouncement("Follow-up created in Pending. The original report is still available for review.");
        }} />
    </aside>}
    {items.map((item) => {
      const blocked = registry.blocks([mutationReportKey(projectId, item.report.id)]);
      const formOpen = followUpReport?.report.id === item.report.id;
      return <article className="job-report-card" key={item.report.id} aria-label={`Report for ${item.report.work_title_at_closeout}`}>
        <JobReportContent item={item} />
        <div className="report-card-actions">
          {!item.source_work_state.deleted && <button type="button" className="button button-secondary" onClick={() => void onOpenWork(item.report.work_item_id, item.report.project_id)}>Open original work</button>}
          <button type="button" className="button button-secondary" disabled={blocked || formOpen} onClick={() => void dismiss(item)}>Dismiss</button>
          <button type="button" className="button button-primary" disabled={blocked || followUpReport !== null} onClick={() => { setFollowUpReport(item); setCreated(null); }}>Create Follow-up</button>
          {item.follow_up_count !== "0" && <span className="field-hint">{item.follow_up_count} follow-up{item.follow_up_count === "1" ? "" : "s"}</span>}
        </div>
        {created?.reportId === item.report.id && <div className="detail-notice" role="status"><p>Follow-up created in Pending.</p><button type="button" className="button button-secondary" onClick={() => void onOpenWork(created.workId, projectId)}>Open work</button></div>}

      </article>;
    })}
    {page?.has_more && <button type="button" className="button button-secondary" disabled={loading} onClick={() => void load(true)}>Load more summaries</button>}
  </section>;
}
