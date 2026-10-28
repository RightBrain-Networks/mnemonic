"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import TranscriptFolderDialog from "@/components/transcript-folder-dialog";
import { invalidateTranscriptReads, transcriptHealthOptions, transcriptKeys, transcriptSettingsOptions } from "@/lib/transcript-queries";
import { warningsForTranscriptDirectory } from "@/lib/transcript-settings";
import { transcriptWarningKey } from "@/lib/transcript-health";
import { errorMessage } from "@/lib/api";
import { readBoundedJson } from "@/lib/bounded-json";
import { finiteInteger, objectValue, sameUuid } from "@/lib/wire-guards";
import { decodeTranscriptImport, decodeTranscriptImportRejection, validTranscriptDirectory, decodeTranscriptProxyRejection, decodeTranscriptSettings, transcriptPath, transcriptSettingsPath, type TranscriptSettings } from "@/lib/transcripts";

type TranscriptIntent = Readonly<{ body: string; operationId: string; action: "rebuild" | "import"; directory?: string }>;
type FolderEdit = { original: string; path: string; revision: number; directories: string[] };

export default function TranscriptSettingsPanel({ projectId, onPendingChange }: { projectId: string; onPendingChange: (pending: boolean) => void }) {
  const client = useQueryClient();
  const savedSettings = useQuery(transcriptSettingsOptions(projectId));
  const health = useQuery(transcriptHealthOptions(projectId));
  const settings = savedSettings.data;
  const [edit, setEdit] = useState<FolderEdit | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState<"add" | "edit" | null>(null);
  const [busy, setBusy] = useState(false);
  const [savingEnabled, setSavingEnabled] = useState<boolean | null>(null);
  const [mutationError, setError] = useState("");
  const error = mutationError || (savedSettings.error ? errorMessage(savedSettings.error) : "");
  const [notice, setNotice] = useState("");
  const [uncertainSave, setUncertainSave] = useState(false);
  const [pending, setPending] = useState<TranscriptIntent | null>(null);
  const pendingRef = useRef<TranscriptIntent | null>(null);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (!pending && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending, busy]);

  async function reloadSettings() {
    setError("");
    const result = await savedSettings.refetch();
    if (mounted.current && result.isSuccess) { setEdit(null); setRemoving(null); setUncertainSave(false); }
  }

  async function save(change: { enabled?: boolean; directories?: string[] }, revision: number, message: string): Promise<TranscriptSettings | null> {
    if (!settings || busyRef.current || pendingRef.current) return null;
    busyRef.current = true; onPendingChange(true);
    setBusy(true); setSavingEnabled(change.enabled ?? null); setError(""); setNotice("");
    try {
      const response = await fetch(transcriptSettingsPath(projectId), { method: "PATCH", cache: "no-store", signal: AbortSignal.timeout(65000), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled: settings.enabled, directories: settings.directories, ...change, expected_revision: revision }) });
      const value = await readBoundedJson(response, 128 * 1024);
      if (!response.ok) {
        const detail = objectValue(value)?.detail;
        throw new Error(typeof detail === "string" ? detail : String(objectValue(detail)?.message || "Unable to save transcript settings."));
      }
      const saved = decodeTranscriptSettings(value);
      await client.cancelQueries({ queryKey: transcriptKeys.settings(projectId), exact: true });
      client.setQueryData(transcriptKeys.settings(projectId), saved);
      void invalidateTranscriptReads(client, projectId);
      if (!mounted.current) return null;
      setEdit(null); setRemoving(null); setNotice(message);
      return saved;
    } catch (failure) {
      if (mounted.current) { setError(errorMessage(failure)); setUncertainSave(true); }
      return null;
    } finally { busyRef.current = false; if (mounted.current) { setBusy(false); setSavingEnabled(null); onPendingChange(false); } }
  }

  async function runOperation(intent: TranscriptIntent) {
    if (busyRef.current) return;
    const retryingUncertainRequest = pendingRef.current !== null;
    busyRef.current = true;
    pendingRef.current = intent; setPending(intent); onPendingChange(true);
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${transcriptPath(projectId)}/${intent.action}`, { method: "POST", cache: "no-store", signal: AbortSignal.timeout(65000), headers: { "Content-Type": "application/json" }, body: intent.body });
      const value = await readBoundedJson(response, 16 * 1024);
      const rejection = decodeTranscriptProxyRejection(response.status, value) || decodeTranscriptImportRejection(response.status, value);
      if (rejection && !retryingUncertainRequest) {
        if (!mounted.current) return;
        pendingRef.current = null; setPending(null); onPendingChange(false); setError(rejection);
        return;
      }
      if (intent.action === "import" && response.status === 200) {
        const result = decodeTranscriptImport(value, projectId, intent.operationId, intent.directory!);
        void invalidateTranscriptReads(client, projectId);
        if (!mounted.current) return;
        pendingRef.current = null; setPending(null); onPendingChange(false);
        setNotice(`${result.imported} transcript${result.imported === 1 ? "" : "s"} imported; ${result.existing} already registered.${result.skipped ? ` ${result.skipped} unsupported entries skipped.` : ""}`);
        return;
      }
      const queued = objectValue(value)?.queued;
      if (intent.action !== "rebuild" || response.status !== 200 || !finiteInteger(queued) || !sameUuid(objectValue(value)?.project_id, projectId) || !sameUuid(objectValue(value)?.client_operation_id, intent.operationId)) {
        const detail = objectValue(value)?.detail;
        throw new Error(typeof detail === "string" ? detail : String(objectValue(detail)?.message || "The outcome is uncertain. Retry the preserved request."));
      }
      void invalidateTranscriptReads(client, projectId);
      if (!mounted.current) return;
      pendingRef.current = null; setPending(null); onPendingChange(false);
      setNotice(`${queued} transcript${queued === 1 ? "" : "s"} queued for rebuilding.`);
    } catch (failure) { if (mounted.current) setError(errorMessage(failure)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  function scan(directory: string) {
    const operationId = crypto.randomUUID();
    void runOperation(Object.freeze({ action: "import", operationId, directory, body: JSON.stringify({ client_operation_id: operationId, directory }) }));
  }
  async function addFolder(directory: string) {
    setBrowsing(null);
    if (!settings) return;
    if (settings.directories.includes(directory)) { setError("This folder is already configured."); return; }
    const saved = await save({ directories: [...settings.directories, directory] }, settings.revision, "Folder added.");
    if (saved?.enabled) scan(directory);
  }
  const locked = busy || Boolean(pending) || uncertainSave;
  return <section className="transcript-management" aria-label="Transcript settings">
    <div className="transcript-management-heading">
      <div><h2>Transcript collection</h2><p>{settings?.enabled === false ? "Collection is off for this project. Existing conversations remain available." : "Collect agent conversations and make them searchable in this project."}</p></div>
      <label className={`semantic-toggle transcript-global-toggle ${(savingEnabled ?? settings?.enabled) ? "selected" : ""}`}>
        <input type="checkbox" role="switch" aria-label="Enable transcripts" checked={savingEnabled ?? settings?.enabled ?? true} disabled={!settings || locked || Boolean(edit)} onChange={(event) => { if (settings) void save({ enabled: event.target.checked }, settings.revision, event.target.checked ? "Transcripts enabled." : "Transcripts disabled. Importing, normalization and indexing are stopped."); }} />
        <span className="semantic-switch" aria-hidden="true"><span /></span><span>Enable transcripts</span>
      </label>
    </div>
    {error && <div className="error-notice" role="alert"><p>{error}</p></div>}
    {notice && <p className="transcript-action-notice" role="status" aria-label="Transcript action result">{notice}</p>}
    {savedSettings.isPending && <p role="status">Loading transcript settings…</p>}
    {!settings && savedSettings.isError && <button className="button button-secondary" onClick={() => void reloadSettings()}>Retry transcript settings</button>}
    {uncertainSave && <div className="error-notice"><p>Reload saved settings before making another change.</p><button className="button button-secondary" disabled={busy || Boolean(pending)} onClick={() => void reloadSettings()}>Reload transcript settings</button></div>}
    {settings && <>
      <div className="transcript-folders-heading"><div><h3>Source folders <span className="artifact-count">{settings.directories.length}</span></h3><p>Scan a folder to import its existing sessions, including subfolders.</p></div><button className="button button-secondary" disabled={locked || Boolean(edit) || !settings.allowed_roots.length} onClick={() => setBrowsing("add")}>Add folder</button></div>
      {!settings.directories.length ? <div className="transcript-folders-empty"><p>No source folders configured.</p><span>{settings.allowed_roots.length ? "Add a folder to bring existing conversations into this project." : "Configure shared transcript sources on the server to browse folders."}</span></div> : <ul className="transcript-source-list" aria-label="Configured transcript folders">{settings.directories.map((directory) => {
        const warnings = warningsForTranscriptDirectory(directory, settings.directories, health.data?.warnings ?? []);
        const editing = edit?.original === directory;
        return <li key={directory} className={warnings.length ? "has-warning" : ""}>
          <div className="transcript-source-row">
            {editing ? <form className="transcript-source-edit" onSubmit={(event) => { event.preventDefault(); if (!edit || !validTranscriptDirectory(edit.path)) return; if (edit.directories.some((path) => path !== edit.original && path === edit.path)) { setError("This folder is already configured."); return; } void save({ directories: edit.directories.map((path) => path === edit.original ? edit.path : path) }, edit.revision, "Folder updated. Scan it to import existing sessions."); }}>
              <label className="field">Folder path<input autoFocus aria-label={`Folder path for ${directory}`} value={edit.path} spellCheck={false} autoComplete="off" disabled={locked} onChange={(event) => setEdit({ ...edit, path: event.target.value })} /></label>
              <div className="transcript-source-actions"><button type="button" className="button button-secondary" disabled={locked} onClick={() => setBrowsing("edit")}>Browse</button><button className="button button-primary" disabled={locked || !validTranscriptDirectory(edit.path) || edit.path === directory}>Save</button><button type="button" className="button button-secondary" disabled={locked} onClick={() => setEdit(null)}>Cancel</button></div>
            </form> : <>
              <div className="transcript-source-info"><code>{directory}</code><span className={warnings.length ? "transcript-source-warning" : "field-hint"}>{warnings.length ? "Transcript access needs attention" : health.isError ? "Access status unavailable" : settings.enabled ? "Source folder" : "Collection disabled"}</span></div>
              <div className="transcript-source-actions"><button className="button button-secondary" disabled={locked || Boolean(edit) || !settings.enabled} aria-label={`Scan ${directory}`} onClick={() => scan(directory)}>Scan</button><button className="button button-secondary" disabled={locked || Boolean(edit)} aria-label={`Edit ${directory}`} onClick={() => { setRemoving(null); setEdit({ original: directory, path: directory, revision: settings.revision, directories: settings.directories }); }}>Edit</button><button className="button button-secondary transcript-remove" disabled={locked || Boolean(edit)} aria-label={`Remove ${directory}`} onClick={() => setRemoving(directory)}>Remove</button></div>
            </>}
          </div>
          {removing === directory && <div className="transcript-remove-confirm"><p>Remove this source folder? Already imported conversations will remain.</p><div className="transcript-source-actions"><button className="button button-secondary transcript-remove" disabled={locked} onClick={() => void save({ directories: settings.directories.filter((path) => path !== directory) }, settings.revision, "Source folder removed.")}>Remove folder</button><button className="button button-secondary" disabled={locked} onClick={() => setRemoving(null)}>Cancel</button></div></div>}
          {warnings.length > 0 && <div className="transcript-row-warnings" role="status" aria-label={`Access warnings for ${directory}`}><ul>{warnings.map((warning) => <li key={transcriptWarningKey(warning)}><strong>{warning.message}</strong><p>{warning.action}</p>{warning.path !== directory && <code>{warning.path}</code>}</li>)}</ul></div>}
        </li>;
      })}</ul>}
      {Boolean(health.data?.warnings_omitted) && <p className="field-hint">Some access details are omitted. Check the affected transcripts below.</p>}
      <details className="transcript-maintenance-details"><summary>Index maintenance</summary><div><p>Reprocess retained conversations. Active sessions wait until their work is released.</p><button className="button button-secondary" disabled={locked || Boolean(edit) || !settings.enabled} onClick={() => { const operationId = crypto.randomUUID(); void runOperation(Object.freeze({ action: "rebuild", operationId, body: JSON.stringify({ client_operation_id: operationId }) })); }}>Rebuild index</button></div></details>
    </>}
    {pending && <div className="error-notice" role="alert"><p>{busy ? (pending.action === "import" ? "Scanning folder…" : "Requesting index rebuild…") : `The ${pending.action} outcome is unconfirmed. Keep this page open and retry the saved request.`}</p>{!busy && <button className="button button-secondary" onClick={() => void runOperation(pending)}>Retry pending {pending.action}</button>}</div>}
    {browsing && <TranscriptFolderDialog projectId={projectId} onClose={() => setBrowsing(null)} onSelect={(directory) => { if (browsing === "edit" && edit) { setEdit({ ...edit, path: directory }); setBrowsing(null); } else void addFolder(directory); }} />}
  </section>;
}
