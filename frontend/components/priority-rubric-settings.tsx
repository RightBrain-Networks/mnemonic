"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import { decodePriorityRubric, validPriorityRubricContent, type PriorityRubric } from "@/lib/priority-rubric";

type Props = {
  projectId: string;
  refreshSignal: number;
  onNotice: (message: string, error?: boolean) => void;
};

export default function PriorityRubricSettings({ projectId, refreshSignal, onNotice }: Props) {
  const [saved, setSaved] = useState<PriorityRubric | null>(null);
  const [latest, setLatest] = useState<PriorityRubric | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [needsReview, setNeedsReview] = useState(false);
  const [error, setError] = useState("");
  const baseline = useRef<PriorityRubric | null>(null);
  const draftRef = useRef("");
  const savingRef = useRef(false);
  const generation = useRef(0);
  const path = `/projects/${encodeURIComponent(projectId)}/priority-rubric`;

  const accept = useCallback((rubric: PriorityRubric) => {
    baseline.current = rubric;
    draftRef.current = rubric.content;
    setSaved(rubric);
    setDraft(rubric.content);
    setLatest(null);
    setNeedsReview(false);
    setError("");
  }, []);

  const load = useCallback(async () => {
    if (savingRef.current) return;
    const request = ++generation.current;
    setLoading(true);
    setError("");
    try {
      const rubric = decodePriorityRubric(await api<unknown>(path), projectId);
      if (request !== generation.current) return;
      if (!baseline.current || draftRef.current === baseline.current.content
        || draftRef.current === rubric.content) {
        accept(rubric);
      } else if (rubric.revision !== baseline.current.revision) {
        setLatest(rubric);
        setNeedsReview(true);
      } else {
        setLatest(rubric);
      }
    } catch (reason) {
      if (request === generation.current) setError(errorMessage(reason));
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [accept, path, projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshSignal]);
  useEffect(() => () => { generation.current += 1; }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!saved || savingRef.current || loading || needsReview || !validPriorityRubricContent(draft)) return;
    const request = ++generation.current;
    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      const rubric = decodePriorityRubric(await api<unknown>(path, {
        method: "PATCH", body: JSON.stringify({ content: draft, expected_revision: saved.revision })
      }), projectId);
      if (request !== generation.current) return;
      if (rubric.content !== draft) throw new Error("The saved rubric did not match your draft.");
      accept(rubric);
      onNotice("Priority rubric saved.");
    } catch (reason) {
      if (request !== generation.current) return;
      setError(errorMessage(reason));
      if (!(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500 || reason.status === 409) {
        setLatest(null);
        setNeedsReview(true);
      }
    } finally {
      savingRef.current = false;
      if (request === generation.current) setSaving(false);
    }
  }

  return <section className="settings-card" aria-labelledby="priority-rubric-title">
    <div className="settings-card-heading"><div>
      <span className="section-label">WORK PRIORITY</span>
      <h2 id="priority-rubric-title">Priority rubric</h2>
    </div></div>
    <p className="settings-intro" id="priority-rubric-hint">
      Set the guidance agents use to choose work priorities for this project.
      Agents request this Markdown when they need it.
    </p>
    <form className="form-stack" onSubmit={(event) => void save(event)}>
      {!saved ? <p role="status">{loading ? "Loading priority rubric…" : "Priority rubric is unavailable."}</p> : <>
        <label className="field" htmlFor="priority-rubric-content">Rubric (Markdown)
          <textarea id="priority-rubric-content" className="priority-rubric-editor" rows={18}
            aria-describedby="priority-rubric-hint" required spellCheck={false}
            disabled={saving} value={draft} onChange={(event) => {
              draftRef.current = event.target.value;
              setDraft(event.target.value);
            }} />
        </label>
        {!validPriorityRubricContent(draft) && <p className="field-hint" role="alert">
          Enter nonblank text, up to 100,000 characters.
        </p>}
      </>}
      {error && <p className="error-notice" role="alert">{error}</p>}
      {needsReview && <div className="error-notice" role="alert">
        <p>Settings changed or the save outcome is uncertain. Your draft has been kept.
          Load and review the saved rubric before saving again.</p>
        {latest && <>
          <label className="field">Currently saved rubric
            <textarea rows={8} readOnly value={latest.content} />
          </label>
          <button type="button" className="button button-secondary" disabled={loading || saving || Boolean(error)}
            onClick={() => {
              baseline.current = latest;
              setSaved(latest);
              setNeedsReview(false);
              setLatest(null);
            }}>I reviewed the saved rubric</button>
        </>}
      </div>}
      <div className="settings-actions">
        <button type="submit" className="button button-primary"
          disabled={!saved || loading || saving || needsReview || draft === saved.content || !validPriorityRubricContent(draft)}>
          {saving ? "Saving…" : "Save priority rubric"}
        </button>
        {(error || needsReview) && <button type="button" className="button button-secondary"
          disabled={loading || saving} onClick={() => void load()}>
          {loading ? "Loading…" : "Load saved rubric"}
        </button>}
      </div>
    </form>
  </section>;
}
