"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import BackupScheduleForm from "@/components/backup-schedule";
import ProjectExportPanel from "@/components/project-export";
import { backupCatalogOptions, backupJobActive, backupJobOptions, secondaryKeys } from "@/lib/secondary-queries";
import { errorMessage } from "@/lib/api";
import { backupAge, backupPath, backupSize, backupJson, backupResponseMessage, decodeBackupJob, definitiveBackupFailure, type ProjectBackups } from "@/lib/backups";
import { readBoundedJson } from "@/lib/bounded-json";
import { objectValue } from "@/lib/wire-guards";
import { formatDateTime } from "@/lib/display-time";
import type { Project } from "@/lib/types";

type Props = { project: Project; maximumBytes: number; onPendingChange: (pending: boolean) => void };

export default function ProjectBackupsPanel({ project, maximumBytes, onPendingChange }: Props) {
  const client = useQueryClient();
  const [visible, setVisible] = useState(() => !document.hidden);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState<"restore" | null>(null);
  const [creating, setCreating] = useState(false);
  const [backupUncertain, setBackupUncertain] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [serverArchive, setServerArchive] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const mounted = useRef(true);
  const allowReload = useRef(false);
  const inFlight = useRef(false);
  const completedJob = useRef<string | null>(null);

  const catalogQuery = useQuery({ ...backupCatalogOptions(project.id), refetchInterval: (query) => !visible ? false : backupJobActive(query.state.data?.latest_job ?? undefined) ? 5_000 : 30_000 });
  const catalog = catalogQuery.data;
  const loading = catalogQuery.isFetching;
  const loadError = catalogQuery.error ? errorMessage(catalogQuery.error) : "";
  const jobId = activeJob ?? (backupJobActive(catalog?.latest_job ?? undefined) ? catalog!.latest_job!.job_id : null);
  const jobQuery = useQuery({ ...backupJobOptions(project.id, jobId ?? ""), enabled: jobId !== null && visible });
  const job = catalog?.latest_job && catalog.latest_job.job_id === jobId && !backupJobActive(catalog.latest_job)
    ? catalog.latest_job : jobQuery.data ?? catalog?.latest_job;
  const running = backupJobActive(job ?? undefined);
  const refresh = async () => {
    const result = await catalogQuery.refetch({ cancelRefetch: false });
    if (!result.error) {
      setBackupUncertain(false);
      if (result.data?.latest_job) client.setQueryData(secondaryKeys.backupJob(project.id, result.data.latest_job.job_id), result.data.latest_job);
      if (jobQuery.error) void jobQuery.refetch();
    }
  };
  const invalidate = () => void client.invalidateQueries({ queryKey: secondaryKeys.backupCatalog(project.id), exact: true });

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const update = () => { setVisible(!document.hidden); setNow(Date.now()); };
    document.addEventListener("visibilitychange", update);
    const timer = window.setInterval(() => { if (!document.hidden) setNow(Date.now()); }, 60_000);
    return () => { document.removeEventListener("visibilitychange", update); window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    const result = job;
    if (!result || backupJobActive(result) || completedJob.current === result.job_id) return;
    completedJob.current = result.job_id;
    client.setQueryData<ProjectBackups>(secondaryKeys.backupCatalog(project.id), (old) => old ? { ...old, latest_job: result } : old);
    setActiveJob(null);
    if (result.state === "succeeded") setNotice(`Backup created for ${project.name}.`);
    void client.invalidateQueries({ queryKey: secondaryKeys.backupCatalog(project.id), exact: true });
  }, [job, client, project.id, project.name]);
  useEffect(() => {
    if (!busy && !uncertain) return;
    const warn = (event: BeforeUnloadEvent) => {
      if (!allowReload.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy, uncertain]);

  function reload() { allowReload.current = true; window.location.reload(); }

  async function createBackup() {
    if (inFlight.current || running || uncertain || backupUncertain) return;
    inFlight.current = true; setCreating(true); setActionError(""); setNotice("");
    try {
      const response = await fetch(`${backupPath(project.id)}/backups`, { method: "POST" });
      const value = await readBoundedJson(response, 4 * 1024 * 1024);
      if (!response.ok) throw new Error(backupResponseMessage(value, "Unable to queue the backup. Refresh backups to check its status."));
      const queued = decodeBackupJob(value, project.id);
      client.setQueryData(secondaryKeys.backupJob(project.id, queued.job_id), queued);
      client.setQueryData<ProjectBackups>(secondaryKeys.backupCatalog(project.id), (old) => old ? { ...old, latest_job: queued } : old);
      if (mounted.current) setActiveJob(queued.job_id);
      invalidate();
    } catch (error) {
      if (mounted.current) { setActionError(`${errorMessage(error)} Refresh backups to inspect the latest job before starting another.`); setBackupUncertain(true); }
    } finally { inFlight.current = false; if (mounted.current) setCreating(false); }
  }

  async function executeRestore() {
    if (inFlight.current || uncertain || !file && !serverArchive || confirmation !== project.slug) return;
    inFlight.current = true;
    setBusy("restore"); setActionError(""); setNotice(""); onPendingChange(true);
    let outcomeUncertain = true;
    try {
      const response = await fetch(`${backupPath(project.id)}/${serverArchive ? `backups/${encodeURIComponent(serverArchive)}/restore` : "restore"}`, { method: "POST",
        headers: { "X-Confirm-Project": project.id, ...(serverArchive ? {} : { "Content-Type": "application/octet-stream" }) }, ...(serverArchive ? {} : { body: file }) });
      const value = await readBoundedJson(response, 4 * 1024 * 1024);
      if (!response.ok) {
        outcomeUncertain = !definitiveBackupFailure(response.status, value);
        throw new Error(backupResponseMessage(value, "The restore failed."));
      }
      const result = objectValue(value);
      if (result?.project_id !== project.id || result.restored !== true) throw new Error("The restore response could not be verified.");
      outcomeUncertain = false;
      if (mounted.current) reload();
    } catch (error) {
      if (!mounted.current) return;
      setUncertain(outcomeUncertain);
      setActionError(outcomeUncertain
        ? "The outcome is uncertain. Reload and inspect the project before starting another action. Do not upload the archive again while a restore may still be running."
        : errorMessage(error));
    } finally {
      inFlight.current = false;
      if (mounted.current) { setBusy(null); onPendingChange(outcomeUncertain); }
    }
  }
  function restore(event: FormEvent<HTMLFormElement>) { event.preventDefault(); void executeRestore(); }
  async function deleteBackup(filename: string) {
    if (deleteBusy) return;
    setDeleteBusy(true); setActionError(""); setNotice("");
    try {
      const result = objectValue(await backupJson(`${backupPath(project.id)}/backups/${encodeURIComponent(filename)}`, { method: "DELETE" }));
      if (result?.project_id !== project.id || result.filename !== filename || result.deleted !== true) throw new Error("The deletion response could not be verified.");
      setDeleting(null); if (serverArchive === filename) { setServerArchive(null); setConfirmation(""); } setNotice("Backup deleted."); invalidate();
    } catch (error) { setActionError(`${errorMessage(error)} Refresh backups to check whether the file is still present.`); }
    finally { setDeleteBusy(false); }
  }

  const disabled = busy !== null || uncertain;
  return <><section className="settings-card project-backups" aria-labelledby="project-backups-title">
    <div className="settings-card-heading"><div><span className="section-label">RECOVERY</span><h2 id="project-backups-title">Project backups</h2></div></div>
    <p className="settings-intro">Save {project.name} to a compressed archive on this server. Each backup contains this project's PostgreSQL data, including artifact metadata and extracted transcript text. Artifact file contents are managed by your separate file backup system. Include the transcript storage directory in that file backup system too.</p>
    {catalog && <div className="backup-storage">
      <span className="field-hint">Server backup location</span><code>{catalog.storage.path}</code>
      <p className="field-hint">Default installs use a private Docker volume. For a host directory, use <code>compose.bind-mounts.yaml</code> and set <code>MNEMONIC_BACKUP_DIR</code> in the server's <code>.env</code> file.</p>
      <p className="backup-disk-space">{catalog.storage.free_bytes === null || catalog.storage.total_bytes === null ? "Disk space is unavailable." : <><strong>{backupSize(catalog.storage.free_bytes)} free</strong><span> of {backupSize(catalog.storage.total_bytes)} total on this volume</span></>}</p>
      {catalog.storage.error && <p className="error-notice" role="alert">{catalog.storage.error}</p>}
    </div>}
    <div className="settings-actions backup-actions">
      <button type="button" className="button button-primary" disabled={disabled || creating || running || backupUncertain || !!catalog?.storage.error} onClick={() => void createBackup()}>{creating ? "Queuing backup…" : "Back up now"}</button>
      <button type="button" className="button button-secondary" disabled={loading || busy !== null} onClick={() => void refresh()}>Refresh backups</button>
    </div>
    {running && <p className="backup-progress" role="status">Backup {job?.state === "pending" ? "queued" : "running"}. You can leave this page; the server will finish the backup.</p>}
    {job?.state === "failed" && <p className="error-notice" role="alert">{job.error?.message}</p>}
    {jobQuery.error && <p className="error-notice" role="alert">Backup progress is temporarily unavailable. The server may still be working. Refresh backups to check.</p>}
    {loading && !catalog && <p className="field-hint" role="status">Checking stored backups…</p>}
    {loadError && <div className="error-notice" role="alert"><p>{loadError}</p>{catalog && <p>The archive list below may be out of date.</p>}</div>}
    {notice && <p className="backup-notice" role="status">{notice}</p>}
    {actionError && <div className="error-notice" role="alert"><p>{actionError}</p>{uncertain && <button type="button" className="button button-secondary" onClick={reload}>Reload and inspect</button>}</div>}
    {catalog && <BackupScheduleForm projectId={project.id} schedule={catalog.schedule} disabled={disabled} onSaved={(schedule) => {
      void client.cancelQueries({ queryKey: secondaryKeys.backupCatalog(project.id), exact: true });
      client.setQueryData<ProjectBackups>(secondaryKeys.backupCatalog(project.id), (old) => old ? { ...old, schedule } : old); invalidate();
    }} />}
    <h3 className="backup-list-title">Stored backups</h3>
    {catalog && catalog.backups.length === 0 && <p className="backup-empty">No backups yet for this project.</p>}
    {catalog && catalog.backups.length > 0 && <ul className="backup-list" aria-label="Stored project backups">
      {catalog.backups.map((backup) => <li key={backup.filename}>
        <div className="backup-details"><span className="backup-filename">{backup.filename}</span><span className="backup-metadata"><time dateTime={backup.created_at} title={backup.created_at}>{formatDateTime(backup.created_at)}</time><span>{backupAge(backup.created_at, now)}</span><span>{backupSize(backup.size_bytes)}</span></span></div>
        <div className="backup-file-actions"><a className="button button-secondary" href={`${backupPath(project.id)}/backups/${encodeURIComponent(backup.filename)}`} download={backup.filename} aria-label={`Download ${backup.filename}`}>Download</a>
        <button type="button" className="button button-secondary" disabled={disabled || creating || running} onClick={() => { setServerArchive(backup.filename); setFile(null); setConfirmation(""); window.requestAnimationFrame(() => document.getElementById("project-backup-confirmation")?.focus()); }} aria-label={`Restore ${backup.filename}`}>Restore</button>
        <button type="button" className="button button-danger" disabled={disabled || deleteBusy} onClick={() => setDeleting(backup.filename)} aria-label={`Delete ${backup.filename}`}>Delete</button></div>
        {deleting === backup.filename && <div className="backup-delete-confirm" role="group" aria-label="Confirm backup deletion"><p>Permanently delete this backup file from the server?</p><button type="button" className="button button-danger" disabled={deleteBusy || disabled} onClick={() => void deleteBackup(backup.filename)}>{deleteBusy ? "Deleting…" : "Delete backup"}</button><button type="button" className="button button-secondary" disabled={deleteBusy} onClick={() => setDeleting(null)}>Cancel</button></div>}
      </li>)}
    </ul>}
    <form className="backup-restore form-stack" onSubmit={restore}>
      <h3>Restore this project</h3>
      <p className="field-hint" id="backup-restore-warning">Restoring replaces the current PostgreSQL data in {project.name}. Changes made after that backup will be lost. Other projects and artifact file contents are unaffected. Restore matching artifact files separately; older metadata may reference missing or changed files. The dashboard reloads when restoration finishes.</p>
      {serverArchive && <p className="field-hint">Selected server backup: <code className="backup-filename">{serverArchive}</code><button type="button" className="button button-secondary" disabled={disabled} onClick={() => { setServerArchive(null); setConfirmation(""); }}>Choose an uploaded archive instead</button></p>}
      <label className="field" htmlFor="project-backup-file">Backup archive
        <input id="project-backup-file" type="file" aria-label="Backup archive" accept=".bz2,application/x-bzip2" disabled={disabled || creating || running} aria-describedby="backup-upload-hint backup-restore-warning" onChange={(event) => {
          const selected = event.target.files?.[0] ?? null;
          setActionError(""); setConfirmation(""); setFile(null); setServerArchive(null);
          if (!selected) return;
          if (!selected.name.toLowerCase().endsWith(".bz2")) { setActionError("Choose a bzip2 backup archive (.bz2)."); return; }
          if (!selected.size || selected.size > maximumBytes) { setActionError(`Choose a nonempty archive no larger than ${backupSize(maximumBytes)}.`); return; }
          setFile(selected);
        }} />
        <span className="field-hint" id="backup-upload-hint">Upload a .bz2 archive created for this project. Maximum compressed size: {backupSize(maximumBytes)}.</span>
      </label>
      <label className="field" htmlFor="project-backup-confirmation">Type {project.slug} to confirm replacement
        <input id="project-backup-confirmation" autoComplete="off" spellCheck={false} value={confirmation} disabled={disabled || creating || running || !file && !serverArchive} onChange={(event) => setConfirmation(event.target.value)} />
      </label>
      <div className="settings-actions backup-actions"><button type="submit" className="button button-danger" disabled={disabled || creating || running || !file && !serverArchive || confirmation !== project.slug}>{busy === "restore" ? "Restoring project…" : serverArchive ? "Restore selected backup" : "Upload and restore"}</button></div>
    </form>
    {busy && <p className="field-hint" role="status">The project is being restored. Large projects can take several minutes. Keep this page open.</p>}
  </section><ProjectExportPanel projectId={project.id} disabled={disabled} /></>;
}
