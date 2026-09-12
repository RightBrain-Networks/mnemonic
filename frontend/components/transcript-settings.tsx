"use client";

import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import { readBoundedJson } from "@/lib/bounded-json";
import { finiteInteger, objectValue, sameUuid } from "@/lib/wire-guards";
import { decodeTranscriptImport, decodeTranscriptImportRejection, validTranscriptDirectory, decodeTranscriptProxyRejection, decodeTranscriptSettings, transcriptPath, transcriptRequest, transcriptSettingsPath, TRANSCRIPT_MAX_BYTES, type TranscriptSettings } from "@/lib/transcripts";

type TranscriptIntent = Readonly<{ body: string; operationId: string; action: "rebuild" | "import"; directory?: string }>;
export default function TranscriptSettingsPanel({ projectId, onPendingChange }: { projectId: string; onPendingChange: (pending: boolean) => void }) {
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
      if (!controller.signal.aborted) { setSettings(saved); setEnabled(saved.enabled); setMaximum(String(saved.max_file_size_bytes)); setUncertainSave(false); }
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
      const response = await fetch(transcriptSettingsPath(projectId), { method: "PATCH", cache: "no-store", signal: AbortSignal.timeout(65000), headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled, max_file_size_bytes: Number(maximum), expected_revision: settings.revision }) });
      const value = await readBoundedJson(response, 128 * 1024);
      if (!response.ok) {
        if (response.status === 409 || response.status >= 500) setUncertainSave(true);
        const detail = objectValue(value)?.detail;
        throw new Error(typeof detail === "string" ? detail : String(objectValue(detail)?.message || "Unable to save transcript settings."));
      }
      const saved = decodeTranscriptSettings(value);
      if (!mounted.current) return;
      setSettings(saved); setEnabled(saved.enabled); setMaximum(String(saved.max_file_size_bytes)); setNotice("Transcript settings saved.");
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
  const validMaximum = /^\d+$/.test(maximum) && finiteInteger(Number(maximum), 1, settings?.operator_max_file_size_bytes ?? TRANSCRIPT_MAX_BYTES);
  const dirty = Boolean(settings && (enabled !== settings.enabled || Number(maximum) !== settings.max_file_size_bytes));
  return <section className="settings-card" aria-labelledby="transcript-settings-title">
    <div className="settings-card-heading"><div><span className="section-label">AGENT TRANSCRIPTS</span><h2 id="transcript-settings-title">Transcript indexing</h2></div><span className="settings-state">{loading ? "Loading…" : settings?.enabled ? "Enabled" : settings ? "Paused" : "Unavailable"}</span></div>
    <p className="settings-intro">Index the primary session and subagent transcripts reported for this project after work leaves Active, or import existing sessions from a shared folder. Indexed transcripts are available to every agent and the dashboard.</p>
    {loading && <p role="status">Loading transcript settings…</p>}
    {error && <div className="error-notice" role="alert"><p>{error}</p></div>}
    {notice && <p role="status">{notice}</p>}
    {!loading && !settings && <button className="button button-secondary" onClick={() => setRefresh((value) => value + 1)}>Retry transcript settings</button>}
    {settings && <>
      <label className="transcript-enabled"><input type="checkbox" checked={enabled} disabled={loading || busy || Boolean(pending) || uncertainSave} onChange={(event) => setEnabled(event.target.checked)} /> Enable transcript indexing</label>
      <p className="field-hint">Pausing preserves indexed text and metadata. New transcripts wait until indexing is enabled again.</p>
      <label className="field transcript-limit" htmlFor="transcript-max-bytes">Maximum transcript size (bytes)<input id="transcript-max-bytes" type="number" min={1} max={settings.operator_max_file_size_bytes} step={1} value={maximum} disabled={loading || busy || Boolean(pending) || uncertainSave} onChange={(event) => setMaximum(event.target.value)} /><span className="field-hint">Per source file; up to {settings.operator_max_file_size_bytes.toLocaleString("en-US")} bytes, as configured by your operator. Larger files retain a failed indexing record.</span></label>
      {uncertainSave && <div className="error-notice"><p>Reload the saved values before making another change.</p><button className="button button-secondary" disabled={busy || Boolean(pending)} onClick={() => setRefresh((value) => value + 1)}>Reload transcript settings</button></div>}
      <div className="settings-actions"><button className="button button-primary" disabled={loading || busy || Boolean(pending) || uncertainSave || !dirty || !validMaximum} onClick={() => void save()}>{busy && !pending ? "Saving…" : "Save transcript settings"}</button></div>
      <div className="transcript-roots"><h3>Shared transcript folders</h3><p className="field-hint">An operator configures the folders Mnemonic can read. Agents must report paths within these shared folders.</p>{settings.allowed_roots.length ? <ul>{settings.allowed_roots.map((root) => <li className="mono break-all" key={root}>{root}</li>)}</ul> : <p>No shared folders are configured. Indexing will report unavailable source paths.</p>}</div>
      <form className="transcript-import" onSubmit={(event) => {
        event.preventDefault();
        if (loading || busy || pending || dirty || uncertainSave || !settings.allowed_roots.length || !validTranscriptDirectory(directory)) return;
        const operationId = crypto.randomUUID();
        void runOperation(Object.freeze({ action: "import", operationId, directory, body: JSON.stringify({ client_operation_id: operationId, directory }) }));
      }}>
        <h3>Import existing transcripts</h3>
        <p className="settings-intro">Import Claude Code and OpenAI Codex .jsonl files from a shared folder, including subfolders and subagent sessions. Sources already registered for this project are skipped.</p>
        <label className="field" htmlFor="transcript-import-directory">Transcript folder<input id="transcript-import-directory" type="text" value={directory} placeholder="/home/jamie/.claude/projects/-srv-fishfood" autoComplete="off" spellCheck={false} disabled={loading || busy || Boolean(pending) || !settings.allowed_roots.length} onChange={(event) => setDirectory(event.target.value)} /><span className="field-hint">Use the absolute path in one of the shared folders above. Up to 5,000 transcript files per import.</span></label>
        <button type="submit" className="button button-secondary" disabled={loading || busy || Boolean(pending) || dirty || uncertainSave || !settings.allowed_roots.length || !validTranscriptDirectory(directory)}>Import transcripts</button>
        {!settings.allowed_roots.length && <p className="field-hint">Configure a shared transcript folder before importing.</p>}
        {!settings.enabled && <p className="field-hint">Imported transcripts will wait until indexing is enabled.</p>}
      </form>
      <div className="transcript-rebuild"><h3>Rebuild index</h3><p className="settings-intro">Delete the indexed text and search index for this project, then read each recorded transcript location again. Active sessions wait until work leaves Active. Source files must still be available.</p><button className="button button-secondary" disabled={loading || busy || Boolean(pending) || !settings.enabled || dirty || uncertainSave} onClick={() => { const operationId = crypto.randomUUID(); void runOperation(Object.freeze({ action: "rebuild", operationId, body: JSON.stringify({ client_operation_id: operationId }) })); }}>Rebuild index</button>{!settings.enabled && <p className="field-hint">Enable indexing before rebuilding.</p>}</div>
    </>}
    {pending && <div className="error-notice" role="alert"><p>{busy ? (pending.action === "import" ? "Importing transcripts…" : "Requesting index rebuild…") : `Keep this page open until the ${pending.action} request is confirmed. Your exact request is preserved.`}</p><p>Operation: <code>{pending.operationId}</code></p><button className="button button-secondary" disabled={busy} onClick={() => void runOperation(pending)}>Retry pending {pending.action}</button></div>}
  </section>;
}
