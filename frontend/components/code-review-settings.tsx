"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { decodeProjectSettings } from "@/lib/job-completion-reports";
import {
  reviewPolicySummary,
  reviewThresholdLabel,
  type CodeReviewSettings,
} from "@/lib/code-review-policy";
import type { ProjectSettings } from "@/lib/types";

const policy = (settings: ProjectSettings): CodeReviewSettings => ({
  code_review_required_min_priority: settings.code_review_required_min_priority,
  code_review_optional_min_priority: settings.code_review_optional_min_priority,
  allow_remediation_code_reviews: settings.allow_remediation_code_reviews,
});

export default function CodeReviewSettingsPanel({
  projectId,
  settings,
  loading,
  onSaved,
  onRetry,
  onNotice,
}: {
  projectId: string;
  settings: ProjectSettings | null;
  loading: boolean;
  onSaved: (settings: ProjectSettings) => void;
  onRetry: () => void;
  onNotice: (message: string, error?: boolean) => void;
}) {
  const available = settings?.project_id === projectId ? settings : null;
  const [draft, setDraft] = useState<CodeReviewSettings | null>(() =>
    available ? policy(available) : null,
  );
  const [revision, setRevision] = useState<string | null>(
    available?.revision ?? null,
  );
  const previous = useRef(available);
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(
    () => () => {
      generation.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (!available) return;
    const prior = previous.current;
    previous.current = available;
    if (revision === available.revision) return;
    if (
      !prior ||
      !draft ||
      JSON.stringify(draft) === JSON.stringify(policy(prior))
    ) {
      setDraft(policy(available));
      setRevision(available.revision);
    } else setConflict(true);
  }, [available, draft, revision]);
  const dirty =
    available &&
    draft &&
    JSON.stringify(draft) !== JSON.stringify(policy(available));
  async function save() {
    if (!draft || !revision || saving || conflict) return;
    const request = ++generation.current;
    setSaving(true);
    setError("");
    try {
      const value = await api<unknown>(
        `/projects/${encodeURIComponent(projectId)}/settings`,
        {
          method: "PATCH",
          body: JSON.stringify({ expected_revision: revision, ...draft }),
        },
      );
      const saved = decodeProjectSettings(value, projectId);
      if (request !== generation.current) return;
      setRevision(saved.revision);
      setDraft(policy(saved));
      previous.current = saved;
      onSaved(saved);
      onNotice("Code review policy saved.");
    } catch (reason) {
      if (request !== generation.current) return;
      setError(errorMessage(reason));
      if (
        !(reason instanceof ApiError) ||
        reason.status === 0 ||
        reason.status >= 500 ||
        reason.code === "project_settings_changed"
      ) {
        setConflict(true);
        onRetry();
      }
    } finally {
      if (request === generation.current) setSaving(false);
    }
  }
  const disabled = loading || saving || conflict || !available;
  return (
    <section
      className="settings-card"
      aria-labelledby="code-review-settings-title"
    >
      <div className="settings-card-heading">
        <div>
          <span className="section-label">CODE QUALITY</span>
          <h2 id="code-review-settings-title">Code reviews</h2>
        </div>
      </div>
      <p className="settings-intro">
        Choose when completed work requires an adversarial review or asks its
        author for a recommendation. Done records implementation completion;
        review does not block dependencies or approve a release.
      </p>
      {!available || !draft ? (
        <p role="status">Waiting for project settings…</p>
      ) : (
        <>
          {(
            [
              [
                "code_review_required_min_priority",
                "Mandatory review at priority",
              ],
              [
                "code_review_optional_min_priority",
                "Agent may recommend review at priority",
              ],
            ] as const
          ).map(([field, label]) => (
            <label
              className="field review-threshold"
              key={field}
              htmlFor={field}
            >
              <span>
                {label}
                <output htmlFor={field}>
                  {reviewThresholdLabel(draft[field])}
                </output>
              </span>
              <input
                id={field}
                type="range"
                min={0}
                max={100}
                step={5}
                value={draft[field]}
                disabled={disabled}
                aria-valuetext={reviewThresholdLabel(draft[field])}
                onChange={(event) =>
                  setDraft({ ...draft, [field]: Number(event.target.value) })
                }
              />
              <span className="review-threshold-endpoints">
                <span>Always</span>
                <span>Never</span>
              </span>
            </label>
          ))}
          <label className="review-policy-toggle">
            <input
              type="checkbox"
              role="switch"
              checked={draft.allow_remediation_code_reviews}
              disabled={disabled}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  allow_remediation_code_reviews: event.target.checked,
                })
              }
            />
            Allow reviews of remediation work
          </label>
          <p className="field-hint">
            Allow one review of fixes from an earlier review. Any further
            remediation can never request a review. Priority thresholds still
            apply.
          </p>
          <p className="review-policy-summary" aria-live="polite">
            {reviewPolicySummary(draft)}
          </p>
          {conflict && (
            <div className="error-notice" role="alert">
              <p>
                Settings changed or the save outcome is uncertain. Your draft
                has been kept. Compare it with the saved policy before saving
                again.
              </p>
              <p>{reviewPolicySummary(available)}</p>
              <button
                className="button button-secondary"
                disabled={loading || saving}
                onClick={() => {
                  setRevision(available.revision);
                  setConflict(false);
                  setError("");
                }}
              >
                I reviewed the saved policy
              </button>
            </div>
          )}
          {error && (
            <p className="error-notice" role="alert">
              {error}
            </p>
          )}
          <div className="settings-actions">
            <button
              type="button"
              className="button button-primary"
              disabled={disabled || !dirty}
              onClick={() => void save()}
            >
              {saving ? "Saving…" : "Save code review policy"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}
