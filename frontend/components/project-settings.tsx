"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage } from "@/lib/api";
import {
  DEFAULT_RECALL_POINTER_TEMPLATE,
  RECALL_POINTER_MACROS
} from "@/lib/work-recall-pointer";
import { decodeProjectSettings, validReportPrompt } from "@/lib/job-completion-reports";
import type { Project, ProjectSettings } from "@/lib/types";

type Props = {
  project?: Project;
  settings: ProjectSettings | null;
  loading: boolean;
  loadError: string;
  onRetry: () => void;
  onSaved: (settings: ProjectSettings) => void;
  onNotice: (message: string, error?: boolean) => void;
};

type PendingAction = "save" | "clear" | "report-save" | "report-reset" | null;

export default function ProjectSettingsPanel({
  project,
  settings,
  loading,
  loadError,
  onRetry,
  onSaved,
  onNotice
}: Props) {
  const storedTemplate = settings && project && settings.project_id === project.id
    ? settings.recall_pointer_template
    : null;
  const effectiveTemplate = storedTemplate ?? DEFAULT_RECALL_POINTER_TEMPLATE;
  const [draft, setDraft] = useState(effectiveTemplate);
  const effectiveReportPrompt = settings?.job_completion_report_prompt ?? "";
  const [reportDraft, setReportDraft] = useState(effectiveReportPrompt);
  const lastReportPrompt = useRef(effectiveReportPrompt);
  const priorSettings = useRef(settings);
  const [editingRevision, setEditingRevision] = useState(settings?.revision ?? null);
  const [conflictRevision, setConflictRevision] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction>(null);
  const [saveError, setSaveError] = useState("");
  const lastEffectiveTemplate = useRef(effectiveTemplate);
  const requestGeneration = useRef(0);

  useEffect(() => {
    const previousTemplate = lastEffectiveTemplate.current;
    if (previousTemplate === effectiveTemplate) return;
    lastEffectiveTemplate.current = effectiveTemplate;
    setDraft((current) => current === previousTemplate ? effectiveTemplate : current);
  }, [effectiveTemplate]);

  useEffect(() => {
    const previous = lastReportPrompt.current;
    if (previous === effectiveReportPrompt) return;
    lastReportPrompt.current = effectiveReportPrompt;
    setReportDraft((current) => current === previous ? effectiveReportPrompt : current);
  }, [effectiveReportPrompt]);

  useEffect(() => {
    if (!settings) return;
    const previous = priorSettings.current;
    priorSettings.current = settings;
    if (settings.revision === editingRevision) return;
    const hadDraft = previous && (
      draft !== (previous.recall_pointer_template ?? DEFAULT_RECALL_POINTER_TEMPLATE)
      || reportDraft !== previous.job_completion_report_prompt
    );
    if (editingRevision === null || !hadDraft) setEditingRevision(settings.revision);
    else setConflictRevision(settings.revision);
  }, [settings, editingRevision, draft, reportDraft]);

  useEffect(() => () => {
    requestGeneration.current += 1;
  }, [project?.id]);

  if (!project) {
    return <section className="empty-state settings-empty">
      <h2>Select a project.</h2>
      <p>Project settings become available after you create or select a workspace.</p>
      <a className="button button-primary" href="/">Open the work library</a>
    </section>;
  }
  const selectedProject = project;
  const settingsAvailable = settings?.project_id === selectedProject.id;

  const unavailable = !settingsAvailable || loading || conflictRevision !== null;
  const dirty = draft !== effectiveTemplate;
  const canClear = storedTemplate !== null || draft !== DEFAULT_RECALL_POINTER_TEMPLATE;

  async function updateTemplate(template: string | null, action: Exclude<PendingAction, null>) {
    const generation = ++requestGeneration.current;
    setPending(action);
    setSaveError("");
    try {
      const value = await api<unknown>(
        `/projects/${encodeURIComponent(selectedProject.id)}/settings`,
        {
          method: "PATCH",
          body: JSON.stringify({ expected_revision: editingRevision, recall_pointer_template: template })
        }
      );
      const saved = decodeProjectSettings(value, selectedProject.id);
      if (generation !== requestGeneration.current) return;
      setEditingRevision(saved.revision);
      onSaved(saved);
      setDraft(saved.recall_pointer_template ?? DEFAULT_RECALL_POINTER_TEMPLATE);
      onNotice(action === "save"
        ? `Recall pointer content saved for “${selectedProject.name}”.`
        : `Custom recall pointer content cleared for “${selectedProject.name}”.`);
    } catch (error) {
      if (generation === requestGeneration.current) {
        setSaveError(errorMessage(error));
        if (error instanceof ApiError && error.code === "project_settings_changed") {
          setConflictRevision(settings?.revision ?? null);
          onRetry();
        } else if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
          setSaveError("The save outcome is uncertain. Reload settings and compare your draft before saving again.");
          setConflictRevision(settings?.revision ?? null);
          onRetry();
        }
      }
    } finally {
      if (generation === requestGeneration.current) setPending(null);
    }
  }

  function save() {
    if (!draft.trim()) {
      setSaveError("Recall pointer content cannot be blank. Use Clear to restore the built-in default.");
      return;
    }
    void updateTemplate(draft, "save");
  }

  function clear() {
    if (storedTemplate === null) {
      setDraft(DEFAULT_RECALL_POINTER_TEMPLATE);
      setSaveError("");
      return;
    }
    void updateTemplate(null, "clear");
  }

  async function updateReportPrompt(reset: boolean) {
    if (!settings || !reset && !validReportPrompt(reportDraft)) {
      setSaveError("Enter a nonblank prompt within 8,000 characters and 16,384 UTF-8 bytes.");
      return;
    }
    const generation = ++requestGeneration.current;
    setPending(reset ? "report-reset" : "report-save");
    setSaveError("");
    try {
      const value = await api<unknown>(`/projects/${encodeURIComponent(selectedProject.id)}/settings`, {
        method: "PATCH",
        body: JSON.stringify({ expected_revision: editingRevision, job_completion_report_prompt: reset ? null : reportDraft })
      });
      const saved = decodeProjectSettings(value, selectedProject.id);
      if (generation !== requestGeneration.current) return;
      setEditingRevision(saved.revision);
      onSaved(saved);
      setReportDraft(saved.job_completion_report_prompt);
      onNotice(reset ? "Job completion report prompt reset to default." : "Job completion report prompt saved.");
    } catch (error) {
      if (generation !== requestGeneration.current) return;
      setSaveError(errorMessage(error));
      if (error instanceof ApiError && error.code === "project_settings_changed"
        || !(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
        setConflictRevision(settings.revision);
        setSaveError("Reloaded settings may have changed. Compare your draft with the saved values before saving again.");
        onRetry();
      }
    } finally {
      if (generation === requestGeneration.current) setPending(null);
    }
  }

  return <div className="settings-stack">
    {conflictRevision && <section className="error-notice" role="alert">
      <p>Review the latest saved settings before applying your draft. Your edits have been kept.</p>
      <details><summary>Current saved values</summary><p className="job-report-prompt">{effectiveTemplate}</p><p className="job-report-prompt">{effectiveReportPrompt}</p></details>
      <button type="button" className="button button-secondary" disabled={loading || !settingsAvailable} onClick={() => { setEditingRevision(settings?.revision ?? null); setConflictRevision(null); setSaveError(""); }}>I reviewed the current settings</button>
    </section>}
    <section className="settings-card">
    <div className="settings-card-heading">
      <div>
        <span className="section-label">AGENT HAND-OFF</span>
        <h2 id="recall-pointer-title">Recall pointer content</h2>
      </div>
      <span className={`settings-state ${!settingsAvailable ? "unavailable" : storedTemplate === null ? "default" : "custom"}`}>
        {!settingsAvailable
          ? loading ? "Loading…" : "Unavailable"
          : storedTemplate === null ? "Built-in default" : "Customized"}
      </span>
    </div>

    <p className="settings-intro">
      Choose what Mnemonic copies when you use Copy recall pointer in this project.
      Macros are replaced with values from the selected work item.
    </p>

    {loadError && <div className="error-notice" role="alert">
      <p>{loadError}</p>
      <button className="button button-secondary" type="button" onClick={onRetry}>Try again</button>
    </div>}

    <div className="field settings-template-field">
      <label htmlFor="recall-pointer-template">Recall pointer content</label>
      <textarea
        id="recall-pointer-template"
        className="settings-template mono"
        rows={10}
        maxLength={100000}
        spellCheck={false}
        value={settingsAvailable ? draft : ""}
        placeholder={loading ? "Loading recall pointer content…" : undefined}
        aria-describedby="recall-pointer-template-hint"
        disabled={unavailable || pending !== null}
        onChange={(event) => {
          setDraft(event.target.value);
          setSaveError("");
        }}
      />
      <span className="field-hint" id="recall-pointer-template-hint">
        Saved only for {selectedProject.name}. Clear removes the custom value and restores the built-in default.
      </span>
    </div>

    {saveError && <div className="error-notice" role="alert"><p>{saveError}</p></div>}

    <div className="settings-actions">
      <button
        className="button button-primary"
        type="button"
        disabled={unavailable || pending !== null || !dirty || !draft.trim()}
        onClick={save}
      >
        {pending === "save" ? "Saving…" : "Save"}
      </button>
      <button
        className="button button-secondary"
        type="button"
        disabled={unavailable || pending !== null || !canClear}
        onClick={clear}
      >
        {pending === "clear" ? "Clearing…" : "Clear"}
      </button>
      {loading && <span className="settings-loading" role="status">Loading project settings…</span>}
    </div>

    <div className="macro-legend" aria-labelledby="macro-legend-title">
      <div>
        <span className="section-label">AVAILABLE MACROS</span>
        <h3 id="macro-legend-title">Values you can insert</h3>
      </div>
      <dl>
        {RECALL_POINTER_MACROS.map(({ macro, description }) => <div key={macro}>
          <dt><code>{macro}</code></dt>
          <dd>{description}</dd>
        </div>)}
      </dl>
      <p>Unknown macros are left unchanged in the copied text.</p>
    </div>
  </section>
    <section className="settings-card" aria-labelledby="job-report-prompt-title">
      <div className="settings-card-heading"><div><span className="section-label">HUMAN SUMMARIES</span><h2 id="job-report-prompt-title">Job completion report prompt</h2></div></div>
      <p className="settings-intro">Agents use these instructions for future Done, Won’t do, and Promoted reports. Reports already written stay unchanged. This is writing guidance; it does not run tools or generate reports in the dashboard.</p>
      <label className="field" htmlFor="job-report-prompt">Job completion report prompt
        <textarea id="job-report-prompt" className="settings-template" rows={18} value={settingsAvailable ? reportDraft : ""}
          disabled={unavailable || pending !== null} onChange={(event) => { setReportDraft(event.target.value); setSaveError(""); }} />
        <span className="field-hint">Assume the reader has seen no other LLM output and is multitasking. Require a concise paragraph and zero or more FYI bullets, each at most three sentences. Blocking questions belong in Needs Attention. Maximum 8,000 characters and 16,384 UTF-8 bytes; no macros.</span>
      </label>
      <div className="settings-actions">
        <button type="button" className="button button-primary" disabled={unavailable || pending !== null || reportDraft === effectiveReportPrompt || !validReportPrompt(reportDraft)} onClick={() => void updateReportPrompt(false)}>{pending === "report-save" ? "Saving…" : "Save"}</button>
        <button type="button" className="button button-secondary" disabled={unavailable || pending !== null} onClick={() => void updateReportPrompt(true)}>{pending === "report-reset" ? "Resetting…" : "Reset to default"}</button>
      </div>
    </section>
  </div>;
}
