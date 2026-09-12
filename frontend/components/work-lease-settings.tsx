"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { decodeProjectSettings } from "@/lib/job-completion-reports";
import {
  leaseSettingsDraft, MAX_LEASE_MINUTES, parseLeaseDraft, sameLeaseDraft,
  type LeaseSettingsDraft
} from "@/lib/work-lease-settings";
import type { ProjectSettings } from "@/lib/types";

export default function WorkLeaseSettings({
  projectId, settings, loading, loadError, onSaved, onRetry, onNotice
}: {
  projectId: string;
  settings: ProjectSettings | null;
  loading: boolean;
  loadError: string;
  onSaved: (settings: ProjectSettings) => void;
  onRetry: () => void;
  onNotice: (message: string, error?: boolean) => void;
}) {
  const available = settings?.project_id === projectId ? settings : null;
  const [draft, setDraft] = useState<LeaseSettingsDraft | null>(
    () => available ? leaseSettingsDraft(available) : null
  );
  const [revision, setRevision] = useState(available?.revision ?? null);
  const acceptedRevision = useRef(available?.revision ?? null);
  const prior = useRef(available);
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);

  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    if (!available || acceptedRevision.current !== null
      && BigInt(available.revision) <= BigInt(acceptedRevision.current)) return;
    if (!draft || !prior.current || sameLeaseDraft(draft, leaseSettingsDraft(prior.current))) {
      prior.current = available;
      setDraft(leaseSettingsDraft(available));
      acceptedRevision.current = available.revision;
      setRevision(available.revision);
    } else {
      // A refresh can finish while the user accepts these saved values. Consult
      // the acknowledged revision when this update runs, not its earlier render.
      setConflict((current) => current || acceptedRevision.current === null
        || BigInt(available.revision) > BigInt(acceptedRevision.current));
    }
  }, [available, draft, revision]);

  const dirty = available && draft && !sameLeaseDraft(draft, leaseSettingsDraft(available));
  const parsed = draft ? parseLeaseDraft(draft) : null;
  const disabled = !available || loading || saving || conflict;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!parsed || !revision || disabled || !dirty) return;
    const request = ++generation.current;
    setSaving(true);
    setError("");
    try {
      const value = await api<unknown>(`/projects/${encodeURIComponent(projectId)}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ expected_revision: revision, ...parsed })
      });
      const saved = decodeProjectSettings(value, projectId);
      if (request !== generation.current) return;
      prior.current = saved;
      setConflict(false);
      acceptedRevision.current = saved.revision;
      setRevision(saved.revision);
      setDraft(leaseSettingsDraft(saved));
      onSaved(saved);
      onNotice("Work lease durations saved.");
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

  return <form className="work-lease-settings" aria-labelledby="work-lease-settings-title"
    onSubmit={(event) => void save(event)}>
    <h3 id="work-lease-settings-title">Work lease durations</h3>
    <p className="settings-intro" id="work-lease-settings-hint">
      Set durations in minutes for this project. Agents use the default for startup and
      investigation, then request time within the minimum and maximum for the work remaining.
    </p>
    {loadError && <div className="error-notice" role="alert">
      <p>{loadError}</p>
      <button type="button" className="button button-secondary" onClick={onRetry}>Try again</button>
    </div>}
    {!available || !draft ? <p role="status">{loading ? "Loading work lease durations…" : "Work lease durations are unavailable."}</p> : <>
      <div className="work-lease-fields">
        {([
          ["lease_default_minutes", "Default (minutes)"],
          ["lease_minimum_minutes", "Minimum (minutes)"],
          ["lease_maximum_minutes", "Maximum (minutes)"]
        ] as const).map(([field, label]) => <label className="field" htmlFor={field} key={field}>
          {label}
          <input id={field} type="number" min={1} max={MAX_LEASE_MINUTES} step={1}
            required value={draft[field]} disabled={disabled}
            aria-describedby={`work-lease-settings-hint${!parsed ? " work-lease-settings-validation" : ""}`}
            aria-invalid={!parsed || undefined}
            onChange={(event) => {
              setDraft({ ...draft, [field]: event.target.value });
              setError("");
            }} />
        </label>)}
      </div>
      {!parsed && <p className="field-hint" role="alert" id="work-lease-settings-validation">
        Enter positive whole minutes. Minimum must be at most Default, and Default must be at most Maximum.
      </p>}
      {conflict && <div className="error-notice" role="alert">
        <p>Settings changed or the save outcome is uncertain. Your draft has been kept. Compare it with the saved durations before saving again.</p>
        <p>Saved durations: Default {available.lease_default_minutes} minutes;
          Minimum {available.lease_minimum_minutes} minutes;
          Maximum {available.lease_maximum_minutes} minutes.</p>
        <button type="button" className="button button-secondary" disabled={loading || saving || Boolean(loadError)}
          onClick={() => {
            prior.current = available;
            acceptedRevision.current = available.revision;
            setRevision(available.revision);
            setConflict(false);
            setError("");
          }}>I reviewed the saved durations</button>
      </div>}
      {error && <p className="error-notice" role="alert">{error}</p>}
      <div className="settings-actions">
        <button type="submit" className="button button-primary" disabled={disabled || !dirty || !parsed}>
          {saving ? "Saving…" : "Save lease durations"}
        </button>
      </div>
    </>}
  </form>;
}
