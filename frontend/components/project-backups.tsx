"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { backupCatalogOptions, backupJobActive, backupJobOptions, secondaryKeys } from "@/lib/secondary-queries";
import { detailMessage, errorMessage } from "@/lib/api";
import { backupAge, backupPath, backupSize, decodeBackupJob, definitiveBackupFailure, type BackupJob } from "@/lib/backups";
import { readBoundedJson } from "@/lib/bounded-json";
import { formatDateTime } from "@/lib/display-time";
import type { Project } from "@/lib/types";

type Props = {
  project: Project;
  maximumBytes: number;
  onPendingChange: (pending: boolean) => void;
};

function responseMessage(value: unknown, fallback: string): string {
  if (!value || typeof value !== "object") return fallback;
  const result = value as { detail?: unknown; error?: unknown };
  return detailMessage(result.detail ?? result.error).message || fallback;
}

export default function ProjectBackupsPanel({ project, maximumBytes, onPendingChange }: Props) {
  const client = useQueryClient();
  const [visible, setVisible] = useState(() => !document.hidden);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const jobWaiter = useRef<{ resolve: (job: BackupJob) => void; reject: (error: unknown) => void } | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<"backup" | "restore" | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const mounted = useRef(true);
  const allowReload = useRef(false);
  const inFlight = useRef(false);

  const catalogQuery = useQuery({ ...backupCatalogOptions(project.id), refetchInterval: busy || !visible ? false : 60_000 });
  const catalog = catalogQuery.data;
  const loading = catalogQuery.isFetching;
  const loadError = catalogQuery.error ? errorMessage(catalogQuery.error) : "";
  const refresh = () => void catalogQuery.refetch({ cancelRefetch: false });
  const jobQuery = useQuery({ ...backupJobOptions(project.id, visible ? activeJob ?? "" : ""), enabled: activeJob !== null && visible });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      jobWaiter.current?.reject(new Error("Backup progress is no longer being observed."));
      jobWaiter.current = null;
    };
  }, []);
  useEffect(() => {
    const update = () => { setVisible(!document.hidden); setNow(Date.now()); };
    document.addEventListener("visibilitychange", update);
    const timer = window.setInterval(() => { if (!document.hidden) setNow(Date.now()); }, 60_000);
    return () => { document.removeEventListener("visibilitychange", update); window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!activeJob || !jobWaiter.current) return;
    if (jobQuery.error) jobWaiter.current.reject(jobQuery.error);
    else if (jobQuery.data && !backupJobActive(jobQuery.data)) jobWaiter.current.resolve(jobQuery.data);
    else return;
    jobWaiter.current = null;
    setActiveJob(null);
  }, [activeJob, jobQuery.data, jobQuery.error]);

  useEffect(() => {
    if (!busy && !uncertain) return;
    const warn = (event: BeforeUnloadEvent) => {
      if (!allowReload.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy, uncertain]);

  function reload() {
    allowReload.current = true;
    window.location.reload();
  }

  function completedBackup(initial: BackupJob): Promise<BackupJob> {
    if (!backupJobActive(initial)) return Promise.resolve(initial);
    client.setQueryData(secondaryKeys.backupJob(project.id, initial.job_id), initial);
    return new Promise<BackupJob>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        jobWaiter.current = null;
        setActiveJob(null);
        reject(new Error("The backup is still queued or running."));
      }, 30 * 60_000);
      jobWaiter.current = {
        resolve: (job) => { window.clearTimeout(timer); resolve(job); },
        reject: (error) => { window.clearTimeout(timer); reject(error); }
      };
      setActiveJob(initial.job_id);
    });
  }

  async function execute(action: "backup" | "restore") {
    if (inFlight.current || uncertain || action === "restore" && (!file || confirmation !== project.slug)) return;
    inFlight.current = true;
    setBusy(action); setActionError(""); setNotice(""); onPendingChange(true);
    let outcomeUncertain = true;
    try {
      const response = await fetch(`${backupPath(project.id)}/${action === "backup" ? "backups" : "restore"}`, {
        method: "POST",
        ...(action === "restore" ? {
          headers: { "Content-Type": "application/octet-stream", "X-Confirm-Project": project.id },
          body: file
        } : {})
      });
      const value = await readBoundedJson(response, 4 * 1024 * 1024);
      if (!response.ok) {
        outcomeUncertain = !definitiveBackupFailure(response.status, value);
        throw new Error(responseMessage(value, "The backup action failed."));
      }
      if (action === "backup") {
        const job = await completedBackup(decodeBackupJob(value, project.id));
        if (job.state === "failed") {
          outcomeUncertain = false;
          throw new Error(job.error!.message);
        }
      }
      else if (!value || typeof value !== "object" || (value as { project_id?: unknown }).project_id !== project.id
        || (value as { restored?: unknown }).restored !== true) throw new Error("The restore response could not be verified.");
      outcomeUncertain = false;
      if (!mounted.current) return;
      if (action === "restore") { reload(); return; }
      setNotice(`Backup created for ${project.name}.`);
      void client.cancelQueries({ queryKey: secondaryKeys.backupCatalog(project.id), exact: true });
      void client.invalidateQueries({ queryKey: secondaryKeys.backupCatalog(project.id), exact: true });
    } catch (error) {
      if (!mounted.current) return;
      setUncertain(outcomeUncertain);
      setActionError(outcomeUncertain
        ? "The outcome is uncertain. Reload and inspect the project and backup list before starting another action. Do not upload the archive again while a restore may still be running."
        : errorMessage(error));
    } finally {
      inFlight.current = false;
      if (mounted.current) { setBusy(null); onPendingChange(outcomeUncertain); }
    }
  }

  function restore(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void execute("restore");
  }

  const disabled = busy !== null || uncertain;
  return <section className="settings-card project-backups" aria-labelledby="project-backups-title">
    <div className="settings-card-heading">
      <div><span className="section-label">RECOVERY</span><h2 id="project-backups-title">Project backups</h2></div>
      {catalog && <span className="settings-state">Keep latest {catalog.retention_count}</span>}
    </div>
    <p className="settings-intro">Back up {project.name} and download its compressed archives. Each archive contains this project's PostgreSQL data, including artifact metadata and extracted transcript text. Artifact file contents are managed by your separate file backup system. Include the transcript storage directory in that file backup system too.</p>
    <div className="settings-actions backup-actions">
      <button type="button" className="button button-primary" disabled={disabled} onClick={() => void execute("backup")}>{busy === "backup" ? "Creating backup…" : "Back up now"}</button>
      <button type="button" className="button button-secondary" disabled={loading || busy !== null} onClick={() => refresh()}>Refresh backups</button>
    </div>
    {loading && <p className="field-hint" role="status">Checking stored backups…</p>}
    {loadError && <div className="error-notice" role="alert"><p>{loadError}</p>{catalog && <p>The archive list below may be out of date.</p>}</div>}
    {notice && <p className="backup-notice" role="status">{notice}</p>}
    {actionError && <div className="error-notice" role="alert"><p>{actionError}</p>{uncertain && <button type="button" className="button button-secondary" onClick={reload}>Reload and inspect</button>}</div>}
    {catalog && catalog.backups.length === 0 && <p className="backup-empty">No backups yet for this project.</p>}
    {catalog && catalog.backups.length > 0 && <ul className="backup-list" aria-label="Stored project backups">
      {catalog.backups.map((backup) => <li key={backup.filename}>
        <div className="backup-details">
          <span className="backup-filename">{backup.filename}</span>
          <span className="backup-metadata"><time dateTime={backup.created_at} title={backup.created_at}>{formatDateTime(backup.created_at)}</time><span>{backupAge(backup.created_at, now)}</span><span>{backupSize(backup.size_bytes)}</span></span>
        </div>
        <a className="button button-secondary" href={`${backupPath(project.id)}/backups/${encodeURIComponent(backup.filename)}`} download={backup.filename} aria-label={`Download ${backup.filename}`}>Download</a>
      </li>)}
    </ul>}
    <form className="backup-restore form-stack" onSubmit={restore}>
      <h3>Restore this project</h3>
      <p className="field-hint" id="backup-restore-warning">Restoring replaces the current PostgreSQL data in {project.name}. Changes made after that backup will be lost. Other projects and artifact file contents are unaffected. Restore matching artifact files separately; older metadata may reference missing or changed files. The dashboard reloads when restoration finishes.</p>
      <label className="field" htmlFor="project-backup-file">Backup archive
        <input id="project-backup-file" type="file" aria-label="Backup archive" accept=".bz2,application/x-bzip2" disabled={disabled} aria-describedby="backup-upload-hint backup-restore-warning" onChange={(event) => {
          const selected = event.target.files?.[0] ?? null;
          setActionError(""); setConfirmation(""); setFile(null);
          if (!selected) return;
          if (!selected.name.toLowerCase().endsWith(".bz2")) { setActionError("Choose a bzip2 backup archive (.bz2)."); return; }
          if (!selected.size || selected.size > maximumBytes) { setActionError(`Choose a nonempty archive no larger than ${backupSize(maximumBytes)}.`); return; }
          setFile(selected);
        }} />
        <span className="field-hint" id="backup-upload-hint">Upload a .bz2 archive created for this project. Maximum compressed size: {backupSize(maximumBytes)}.</span>
      </label>
      <label className="field" htmlFor="project-backup-confirmation">Type {project.slug} to confirm replacement
        <input id="project-backup-confirmation" autoComplete="off" spellCheck={false} value={confirmation} disabled={disabled || !file} onChange={(event) => setConfirmation(event.target.value)} />
      </label>
      <div className="settings-actions backup-actions"><button type="submit" className="button button-danger" disabled={disabled || !file || confirmation !== project.slug}>{busy === "restore" ? "Restoring project…" : "Upload and restore"}</button></div>
    </form>
    {busy && <p className="field-hint" role="status">{busy === "backup" ? "The backup is queued or running." : "The project is being restored."} Large projects can take several minutes. Keep this page open.</p>}
  </section>;
}
