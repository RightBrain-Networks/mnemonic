"use client";

import { useEffect, useRef, useState } from "react";
import { transcriptBytes, transcriptMegabytes } from "@/lib/transcript-settings";
import { errorMessage } from "@/lib/api";
import { readBoundedJson } from "@/lib/bounded-json";
import { finiteInteger, objectValue, sameUuid } from "@/lib/wire-guards";
import { decodeTranscriptImport, decodeTranscriptImportRejection, validTranscriptDirectory, decodeTranscriptProxyRejection, decodeTranscriptSettings, transcriptPath, transcriptRequest, transcriptSettingsPath, TRANSCRIPT_MAX_BYTES, type TranscriptSettings } from "@/lib/transcripts";

type TranscriptIntent = Readonly<{ body: string; operationId: string; action: "rebuild" | "import"; directory?: string }>;
export default function TranscriptSettingsPanel({ projectId, onPendingChange }: { projectId: string; onPendingChange: (pending: boolean) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<TranscriptSettings | null>(null);
  const [enabled, setEnabled] = useState(true);
  const [maximum, setMaximum] = useState("");
  const [directory, setDirectory] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [uncertainSave, setUncertainSave] = useState(false);
  const [pending, setPending] = useState<TranscriptIntent | null>(null);
  const pendingRef = useRef<TranscriptIntent | null>(null);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void transcriptRequest(transcriptSettingsPath(projectId), { signal: controller.signal }).then((value) => {
      const saved = decodeTranscriptSettings(value);
      if (!controller.signal.aborted) { setSettings(saved); setEnabled(saved.enabled); setMaximum(transcriptMegabytes(saved.max_file_size_bytes)); setUncertainSave(false); }
    }).catch((error) => { if (!controller.signal.aborted) setError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [projectId, refresh]);
  useEffect(() => {
    if (!pending) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending]);

  async function save() {
    if (!settings || busyRef.current || pendingRef.current) return;
    busyRef.current = true;
    setBusy(true); setError(""); setNotice("");
    try {
      const response = await fetch(transcriptSettingsPath(projectId), { method: "PATCH", cache: "no-store", signal: AbortSignal.timeout(65000), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, max_file_size_bytes: transcriptBytes(maximum), expected_revision: settings.revision }) });
      const value = await readBoundedJson(response, 128 * 1024);
      if (!response.ok) {
        if (response.status === 409 || response.status >= 500) setUncertainSave(true);
        const detail = objectValue(value)?.detail;
        throw new Error(typeof detail === "string" ? detail : String(objectValue(detail)?.message || "Unable to save transcript settings."));
      }
      const saved = decodeTranscriptSettings(value);
      if (!mounted.current) return;
      setSettings(saved); setEnabled(saved.enabled); setMaximum(transcriptMegabytes(saved.max_file_size_bytes)); setNotice("Transcript settings saved.");
    } catch (error) { if (mounted.current) { setError(errorMessage(error)); setUncertainSave(true); } }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
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
      const rejection = decodeTranscriptProxyRejection(response.status, value)
        || (intent.action === "import" ? decodeTranscriptImportRejection(response.status, value) : null);
      if (rejection && !retryingUncertainRequest) {
        if (!mounted.current) return;
        pendingRef.current = null; setPending(null); onPendingChange(false);
        setError(rejection);
        return;
      }
      if (intent.action === "import" && response.status === 200) {
        const result = decodeTranscriptImport(value, projectId, intent.operationId, intent.directory!);
        if (!mounted.current) return;
        pendingRef.current = null; setPending(null); onPendingChange(false);
        setNotice(`${result.imported} transcript${result.imported === 1 ? "" : "s"} imported; ${result.existing} already registered.${result.skipped ? ` ${result.skipped} entries skipped because they are links or unsupported files or paths.` : ""} Indexing progress is available in Transcripts.`);
        return;
      }
      const queued = objectValue(value)?.queued;
      if (intent.action !== "rebuild" || response.status !== 200 || !finiteInteger(queued) || !sameUuid(objectValue(value)?.project_id, projectId) || !sameUuid(objectValue(value)?.client_operation_id, intent.operationId)) {
        const detail = objectValue(value)?.detail;
        throw new Error(typeof detail === "string" ? detail : String(objectValue(detail)?.message || "The outcome is uncertain. Retry the preserved request."));
      }
      if (!mounted.current) return;
      pendingRef.current = null; setPending(null); onPendingChange(false);
      setNotice(`${queued} transcript${queued === 1 ? "" : "s"} queued for rebuilding. Indexing progress is available in Transcripts.`);
    } catch (error) { if (mounted.current) setError(errorMessage(error)); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }
  const bytes = transcriptBytes(maximum);
  const validMaximum = bytes !== null && finiteInteger(bytes, 1, settings?.operator_max_file_size_bytes ?? TRANSCRIPT_MAX_BYTES);
  const dirty = Boolean(settings && (enabled !== settings.enabled || bytes !== settings.max_file_size_bytes));
  return <section className={`transcript-tools ${expanded ? "is-open" : ""}`} aria-label="Transcript indexing">
    <div className="transcript-tools-heading"><button className="text-button" type="button" aria-expanded={expanded} aria-controls="transcript-settings-panel" disabled={Boolean(pending)} onClick={() => setExpanded(!expanded)}>Transcript indexing <span className="library-tools-chevron" aria-hidden="true"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true"><path d="m6 15 6-6 6 6" /></svg></span></button><span className="settings-state">{loading ? "Loading…" : settings?.enabled ? "Enabled" : settings ? "Paused" : "Unavailable"}</span></div>
    <div className="library-tools-region" id="transcript-settings-panel" aria-hidden={!expanded} inert={!expanded}><div className="library-tools-clip"><div className="transcript-tools-content">
      {error && <div className="error-notice" role="alert"><p>{error}</p></div>}
      {notice && <p role="status">{notice}</p>}
      {!loading && !settings && <button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Retry transcript settings</button>}
      {settings && <>
        <div className="transcript-settings-row">
          <label className="transcript-enabled"><input type="checkbox" aria-label="Enable transcript indexing" checked={enabled} disabled={loading || busy || Boolean(pending) || uncertainSave} onChange={(event) => setEnabled(event.target.checked)} /> Enable indexing</label>
          <label className="field" htmlFor="transcript-max-mb">Maximum transcript size (MB)<input id="transcript-max-mb" type="number" min={0} max={Number(transcriptMegabytes(settings.operator_max_file_size_bytes))} step={1} value={maximum} disabled={loading || busy || Boolean(pending) || uncertainSave} onChange={(event) => setMaximum(event.target.value)} /></label>
          <button className="button button-primary" aria-label="Save transcript settings" disabled={loading || busy || Boolean(pending) || uncertainSave || !dirty || !validMaximum} onClick={() => void save()}>{busy && !pending ? "Saving…" : "Save settings"}</button>
        </div>
        <p className="field-hint">Sizes use binary megabytes (1 MB = 1,048,576 bytes). Operator maximum: {transcriptMegabytes(settings.operator_max_file_size_bytes)} MB. Larger files retry after the limit is raised. Pausing preserves indexed conversations.</p>
        {uncertainSave && <div className="error-notice"><p>Reload the saved values before making another change.</p><button className="button button-secondary" disabled={busy || Boolean(pending)} onClick={() => setRefresh((value) => value + 1)}>Reload transcript settings</button></div>}
        <div className="transcript-maintenance">
          <form className="transcript-import" onSubmit={(event) => {
            event.preventDefault();
            if (loading || busy || pending || dirty || uncertainSave || !settings.allowed_roots.length || !validTranscriptDirectory(directory)) return;
            const operationId = crypto.randomUUID();
            void runOperation(Object.freeze({ action: "import", operationId, directory, body: JSON.stringify({ client_operation_id: operationId, directory }) }));
          }}>
            <label className="field" htmlFor="transcript-import-directory">Import existing sessions<input id="transcript-import-directory" aria-label="Transcript folder" type="text" value={directory} placeholder="Absolute path to a shared transcript folder" autoComplete="off" spellCheck={false} disabled={loading || busy || Boolean(pending) || !settings.allowed_roots.length} onChange={(event) => setDirectory(event.target.value)} /></label>
            <button type="submit" className="button button-secondary" disabled={loading || busy || Boolean(pending) || dirty || uncertainSave || !settings.allowed_roots.length || !validTranscriptDirectory(directory)}>Import transcripts</button>
          </form>
          <div><button className="button button-secondary" disabled={loading || busy || Boolean(pending) || !settings.enabled || dirty || uncertainSave} onClick={() => { const operationId = crypto.randomUUID(); void runOperation(Object.freeze({ action: "rebuild", operationId, body: JSON.stringify({ client_operation_id: operationId }) })); }}>Rebuild index</button><p className="field-hint">Rebuilds from retained conversations. Existing text stays available; active sessions wait.</p></div>
        </div>
        <details className="transcript-roots"><summary>Shared transcript folders ({settings.allowed_roots.length})</summary>{settings.allowed_roots.length ? <ul>{settings.allowed_roots.map((root) => <li className="mono break-all" key={root}>{root}</li>)}</ul> : <p>No shared folders are configured. Configure a source before importing.</p>}</details>
      </>}
      {pending && <div className="error-notice" role="alert"><p>{busy ? (pending.action === "import" ? "Importing transcripts…" : "Requesting index rebuild…") : `Keep this page open until the ${pending.action} request is confirmed. Your exact request is preserved.`}</p><p>Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled={busy} onClick={() => void runOperation(pending)}>Retry pending {pending.action}</button></div>}
    </div></div></div>
  </section>;
}
