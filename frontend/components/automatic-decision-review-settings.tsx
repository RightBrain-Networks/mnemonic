"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "@/components/dashboard-link";
import DomainDecisionReviewSettings from "@/components/domain-decision-review-settings";
import { api, ApiError, errorMessage } from "@/lib/api";
import { parseDecisionReviewPriority } from "@/lib/automatic-decision-reviews";
import { decodeProjectSettings } from "@/lib/job-completion-reports";
import type { ProjectSettings } from "@/lib/types";

type Draft = { enabled: boolean; priority: string };
const fromSettings = (settings: ProjectSettings): Draft => ({
  enabled: settings.automatic_decision_reviews,
  priority: String(settings.decision_review_priority)
});
const sameDraft = (left: Draft, right: Draft) => left.enabled === right.enabled && left.priority === right.priority;

export default function AutomaticDecisionReviewSettings({
  projectId, settings, loading, loadError, onSaved, onRetry, onNotice, onPendingChange
}: {
  projectId: string;
  settings: ProjectSettings | null;
  loading: boolean;
  loadError: string;
  onSaved: (settings: ProjectSettings) => void;
  onRetry: () => void;
  onNotice: (message: string, error?: boolean) => void;
  onPendingChange: (pending: boolean) => void;
}) {
  const available = settings?.project_id === projectId ? settings : null;
  const [draft, setDraft] = useState<Draft | null>(() => available ? fromSettings(available) : null);
  const [revision, setRevision] = useState(available?.revision ?? null);
  const acceptedRevision = useRef(available?.revision ?? null);
  const previous = useRef(available);
  const generation = useRef(0);
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    if (!available || acceptedRevision.current !== null
      && BigInt(available.revision) <= BigInt(acceptedRevision.current)) return;
    if (!draft || !previous.current || sameDraft(draft, fromSettings(previous.current))) {
      previous.current = available;
      acceptedRevision.current = available.revision;
      setDraft(fromSettings(available));
      setRevision(available.revision);
    } else {
      setConflict((current) => current || acceptedRevision.current === null
        || BigInt(available.revision) > BigInt(acceptedRevision.current));
    }
  }, [available, draft, revision]);

  const priority = draft ? parseDecisionReviewPriority(draft.priority) : null;
  const dirty = available && draft && !sameDraft(draft, fromSettings(available));
  const disabled = !available || loading || saving || conflict;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!draft || !revision || priority === null || disabled || !dirty) return;
    const request = ++generation.current;
    setSaving(true);
    setError("");
    try {
      const value = await api<unknown>(`/projects/${encodeURIComponent(projectId)}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ expected_revision: revision,
          automatic_decision_reviews: draft.enabled, decision_review_priority: priority })
      });
      const saved = decodeProjectSettings(value, projectId);
      if (request !== generation.current) return;
      previous.current = saved;
      acceptedRevision.current = saved.revision;
      setRevision(saved.revision);
      setDraft(fromSettings(saved));
      setConflict(false);
      onSaved(saved);
      onNotice("Automatic decision impact review settings saved.");
    } catch (reason) {
      if (request !== generation.current) return;
      setError(errorMessage(reason));
      if (!(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500
        || reason.code === "project_settings_changed") {
        setConflict(true);
        onRetry();
      }
    } finally {
      if (request === generation.current) setSaving(false);
    }
  }

  return <section className="settings-card" aria-labelledby="automatic-decision-reviews-title">
    <div className="settings-card-heading"><div>
      <span className="section-label">DECISION CHANGES</span>
      <h2 id="automatic-decision-reviews-title">Automatic decision impact reviews</h2>
    </div></div>
    {loadError && <div className="error-notice" role="alert">
      <p>{loadError}</p>
      <button type="button" className="button button-secondary" onClick={onRetry}>Try again</button>
    </div>}
    {!available || !draft ? <p role="status">{loading ? "Loading automatic decision impact review settings…" : "Automatic decision impact review settings are unavailable."}</p> :
      <form className="form-stack" onSubmit={(event) => void save(event)}>
        <label className="review-policy-toggle" htmlFor="automatic-decision-reviews">
          <input id="automatic-decision-reviews" type="checkbox" role="switch"
            checked={draft.enabled} disabled={disabled}
            aria-describedby="automatic-decision-reviews-description"
            onChange={(event) => {
              setDraft({ enabled: event.target.checked,
                priority: priority === null && !event.target.checked ? String(available.decision_review_priority) : draft.priority });
              setError("");
            }} />
          <strong>Project default: automatically create a review work item when a decision document changes.</strong>
        </label>
        <p className="settings-intro" id="automatic-decision-reviews-description">
          This spawned work item will contain a &quot;decision impact review&quot; task to review code that was written
          against the previous version of the decision document. This ensures that when decisions change,
          if necessary, that downstream code changes too. Each knowledge domain can override this default below.
        </p>
        <div className="field decision-review-priority">
          <label htmlFor="decision-review-priority">Decision impact review priority</label>
          <input id="decision-review-priority" type="number" min={0} max={100} step={1}
            required value={draft.priority} disabled={disabled}
            aria-describedby="decision-review-priority-hint"
            aria-invalid={priority === null || undefined}
            onChange={(event) => { setDraft({ ...draft, priority: event.target.value }); setError(""); }} />
          <span className="field-hint" id="decision-review-priority-hint">
            Enter the priority for new decision impact review work items in every domain, from 0 to 100. Higher values are more urgent.
          </span>
          {priority === null && <p className="field-hint" role="alert">Enter a whole number from 0 to 100.</p>}
        </div>
        <p className="field-hint">Edit the investigation instructions in <Link href="/settings/prompts">Prompts</Link>.</p>
        {conflict && <div className="error-notice" role="alert">
          <p>Settings changed or the save outcome is uncertain. Your draft has been kept. Compare it with the saved settings before saving again.</p>
          <p>Saved settings: automatic decision impact reviews {available.automatic_decision_reviews ? "on" : "off"}; priority {available.decision_review_priority}.</p>
          <button type="button" className="button button-secondary" disabled={loading || saving || Boolean(loadError)}
            onClick={() => {
              previous.current = available;
              acceptedRevision.current = available.revision;
              setRevision(available.revision);
              setConflict(false);
              setError("");
            }}>I reviewed the saved decision impact review settings</button>
        </div>}
        {error && <p className="error-notice" role="alert">{error}</p>}
        <div className="settings-actions"><button type="submit" className="button button-primary"
          disabled={disabled || !dirty || priority === null}>{saving ? "Saving…" : "Save decision impact review settings"}</button></div>
      </form>}
    {available && <DomainDecisionReviewSettings projectId={projectId}
      projectEnabled={available.automatic_decision_reviews} onNotice={onNotice} onPendingChange={onPendingChange} />}
  </section>;
}
