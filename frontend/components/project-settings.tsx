"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import {
  DEFAULT_RECALL_POINTER_TEMPLATE,
  RECALL_POINTER_MACROS
} from "@/lib/work-recall-pointer";
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

type PendingAction = "save" | "clear" | null;

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

  const unavailable = !settingsAvailable || loading || Boolean(loadError);
  const dirty = draft !== effectiveTemplate;
  const canClear = storedTemplate !== null || draft !== DEFAULT_RECALL_POINTER_TEMPLATE;

  async function updateTemplate(template: string | null, action: Exclude<PendingAction, null>) {
    const generation = ++requestGeneration.current;
    setPending(action);
    setSaveError("");
    try {
      const saved = await api<ProjectSettings>(
        `/projects/${encodeURIComponent(selectedProject.id)}/settings`,
        {
          method: "PATCH",
          body: JSON.stringify({ recall_pointer_template: template })
        }
      );
      if (generation !== requestGeneration.current) return;
      onSaved(saved);
      setDraft(saved.recall_pointer_template ?? DEFAULT_RECALL_POINTER_TEMPLATE);
      onNotice(action === "save"
        ? `Recall pointer content saved for “${selectedProject.name}”.`
        : `Custom recall pointer content cleared for “${selectedProject.name}”.`);
    } catch (error) {
      if (generation === requestGeneration.current) setSaveError(errorMessage(error));
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

  return <section className="settings-card">
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
  </section>;
}
