"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { JobReportEnvelope, JobReportFollowUpResult } from "@/lib/types";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { dashboardMutationActor } from "@/lib/work-events";
import { errorMessage } from "@/lib/api";
import { mutationCreateKey, mutationReportKey, useMutationIntentRegistry, useMutationScope } from "@/lib/mutation-intent";

export default function JobReportFollowUpForm({ item, onCancel, onCreated }: {
  item: JobReportEnvelope;
  onCancel: () => void;
  onCreated: (result: JobReportFollowUpResult) => void;
}) {
  const registry = useMutationIntentRegistry();
  const { report } = item;
  const keys = [mutationCreateKey(report.project_id), mutationReportKey(report.project_id, report.id)];
  const { blocked } = useMutationScope({ conflictKeys: keys });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const titleRef = useRef<HTMLInputElement>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; titleRef.current?.focus(); return () => { active.current = false; }; }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (blocked || saving) return;
    const fields = new FormData(event.currentTarget);
    const actor = dashboardMutationActor(dashboardSessionId());
    setSaving(true);
    setError("");
    try {
      const result = await registry.execute({
        kind: "create_job_completion_report_follow_up", slot: `report-follow-up:${report.project_id}:${report.id}`,
        projectId: report.project_id, conflictKeys: keys, method: "POST",
        path: `/projects/${report.project_id}/job-completion-reports/${report.id}/follow-ups`,
        expectedSourceWorkItemId: report.work_item_id,
        payload: {
          title: String(fields.get("title") ?? ""), summary: String(fields.get("summary") ?? ""),
          priority: Number(fields.get("priority") ?? 0), actor,
          initial_checkpoint: {
            prompt: String(fields.get("prompt") ?? ""), source_client: actor.actor_client,
            source_session_id: actor.actor_session_id, source_model: null
          }
        }
      });
      if (active.current) onCreated(result);
    } catch (failure) { if (active.current) setError(errorMessage(failure)); }
    finally { if (active.current) setSaving(false); }
  }
  return <form className="form-stack report-follow-up-form" aria-label="Create Follow-up" onSubmit={(event) => void submit(event)}>
    <h4>Create Follow-up</h4>
    <p>Write the change you want. This creates a new Pending work item linked to report <span className="mono">{report.id}</span> and original work <span className="mono">{report.work_item_id}</span>. No agent is assigned.</p>
    <label className="field">Title<input ref={titleRef} name="title" required maxLength={200} defaultValue={`Follow up: ${report.work_title_at_closeout}`.slice(0, 200)} disabled={blocked || saving} /></label>
    <label className="field">Work summary<textarea name="summary" required rows={3} maxLength={1000} placeholder="Briefly describe the new objective." disabled={blocked || saving} /></label>
    <label className="field field-half">Priority<input name="priority" type="number" min={0} max={100} defaultValue={0} disabled={blocked || saving} /></label>
    <label className="field">Initial context and requested change<textarea name="prompt" required rows={6} maxLength={100000} placeholder="Write standalone instructions, for example: Change the dashboard font from Arial to Comic Sans. Include the intended result and any constraints." disabled={blocked || saving} /></label>
    {error && <p className="error-notice" role="alert">{error}</p>}
    <div className="dialog-actions">
      <button type="button" className="button button-secondary" disabled={blocked || saving} onClick={onCancel}>Cancel</button>
      <button type="submit" className="button button-primary" disabled={blocked || saving}>{saving ? "Creating…" : "Create pending work"}</button>
    </div>
  </form>;
}
