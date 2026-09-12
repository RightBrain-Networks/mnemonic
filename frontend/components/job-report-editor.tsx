"use client";

import { useEffect, useId, useState, type Dispatch, type SetStateAction } from "react";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";
import { api, errorMessage } from "@/lib/api";
import { decodeProjectSettings, type JobReportDraft } from "@/lib/job-completion-reports";
import type { ProjectSettings } from "@/lib/types";

export default function JobReportEditor({ projectId, workItemId, draft, onChange, disabled = false }: {
  projectId: string;
  workItemId: string;
  draft: JobReportDraft;
  onChange: Dispatch<SetStateAction<JobReportDraft>>;
  disabled?: boolean;
}) {
  const id = useId();
  const [loaded, setLoaded] = useState<{ workItemId: string; settings: ProjectSettings } | null>(null);
  const settings = loaded?.workItemId === workItemId && loaded.settings.project_id === projectId
    ? loaded.settings : null;
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api<unknown>(`/projects/${projectId}/settings?work_item_id=${encodeURIComponent(workItemId)}`, { signal: controller.signal })
      .then((value) => {
        const next = decodeProjectSettings(value, projectId);
        if (controller.signal.aborted) return;
        setLoaded({ workItemId, settings: next });
      }).catch((failure) => { if (!controller.signal.aborted) setError(errorMessage(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, workItemId, refresh]);
  // Text edits can land between the settings response and its React render.
  // Reconcile an uninitialized revision from the successful current-work read;
  // an already-authored revision still requires the explicit review action below.
  useEffect(() => {
    if (settings && !loading && !error && draft.promptRevision === null) {
      onChange((current) => current.promptRevision === null
        ? { ...current, promptRevision: settings.revision } : current);
    }
  }, [settings, loading, error, draft.promptRevision, onChange]);
  useFailedReadRetry({ scope: `report-prompt:${projectId}:${workItemId}`, failed: Boolean(error), busy: loading, retry: () => setRefresh((value) => value + 1) });
  const stale = settings && draft.promptRevision !== null && settings.revision !== draft.promptRevision;
  return <fieldset className="job-report-editor form-stack" disabled={disabled}>
    <legend>Human-facing job completion report</legend>
    <p className="field-hint">Assume the reader has seen no other LLM output and is multitasking. Explain the result in one concise paragraph with familiar words. Write each FYI as one specific point, preferably one or two sentences, never more than three. Blocking questions belong in Needs Attention.</p>
    {settings && <details className="edit-context">
      <summary>Project report instructions · revision {settings.revision}</summary>
      <p className="job-report-prompt" dir="auto">{settings.job_completion_report_prompt}</p>
    </details>}
    {loading && <p role="status">Loading project report instructions…</p>}
    {error && <p className="error-notice" role="alert">{error}</p>}
    {stale && <p role="alert">Project instructions changed. Review the latest instructions and your report before accepting this revision.</p>}
    <div className="settings-actions">
      <button type="button" className="button button-secondary" disabled={disabled || loading} onClick={() => setRefresh((value) => value + 1)}>Review current prompt</button>
      {stale && <button type="button" className="button button-secondary" onClick={() => onChange((current) => ({ ...current, promptRevision: settings.revision }))}>Use reviewed revision {settings.revision}</button>}
    </div>
    <label className="field" htmlFor={`${id}-summary`}>Human summary
      <textarea id={`${id}-summary`} rows={4} value={draft.summary}
        onChange={(event) => { const summary = event.target.value; onChange((current) => ({ ...current, summary })); }}
        aria-describedby={`${id}-summary-help`} />
      <span className="field-hint" id={`${id}-summary-help`}>One paragraph, usually 50–100 words; at most 2,000 characters and 8,000 UTF-8 bytes. Stored exactly as entered.</span>
    </label>
    {draft.fyiItems.map((item, index) => <div className="report-fyi-field" key={index}>
      <label className="field" htmlFor={`${id}-fyi-${index}`}>FYI {index + 1}
        <textarea id={`${id}-fyi-${index}`} rows={2} value={item} onChange={(event) => {
          const value = event.target.value;
          onChange((current) => ({ ...current, fyiItems: current.fyiItems.map((text, position) => position === index ? value : text) }));
        }} />
        <span className="field-hint">One bullet; at most 600 characters and 2,400 UTF-8 bytes.</span>
      </label>
      <button type="button" className="button button-secondary" aria-label={`Remove FYI ${index + 1}`} onClick={() => onChange((current) => ({ ...current, fyiItems: current.fyiItems.filter((_, position) => position !== index) }))}>Remove</button>
    </div>)}
    <button type="button" className="button button-secondary" disabled={disabled || draft.fyiItems.length >= 10} onClick={() => onChange((current) => ({ ...current, fyiItems: [...current.fyiItems, ""] }))}>Add FYI</button>
  </fieldset>;
}
