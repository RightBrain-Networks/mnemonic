import { readBoundedJson } from "./bounded-json.ts";
import { objectValue, validUtcDateTime, validUuid } from "./wire-guards.ts";

export const BACKUP_DEFAULT_MAX_BYTES = 67_108_864;
export const BACKUP_FILENAME = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.bz2$/;

export type ProjectBackup = { filename: string; created_at: string; size_bytes: number };
export type BackupJob = {
  project_id: string;
  job_id: string;
  state: "pending" | "running" | "succeeded" | "failed";
  result?: ProjectBackup;
  error?: { code: string; message: string };
};
export type ProjectBackups = {
  project_id: string;
  schedule: BackupSchedule;
  storage: BackupStorage;
  latest_job: BackupJob | null;
  backups: ProjectBackup[];
};

export type BackupSchedule = { enabled: boolean; time: string; timezone: string; retention_days: number };
export type BackupStorage = { path: string; total_bytes: number | null; free_bytes: number | null; error: string | null };

export function decodeBackupSchedule(value: unknown): BackupSchedule {
  const schedule = objectValue(value);
  if (!schedule || typeof schedule.enabled !== "boolean" || typeof schedule.time !== "string"
    || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(schedule.time) || typeof schedule.timezone !== "string"
    || !schedule.timezone || !Number.isSafeInteger(schedule.retention_days)
    || (schedule.retention_days as number) < 0 || (schedule.retention_days as number) > 36500) {
    throw new Error("The backup service returned an invalid nightly schedule.");
  }
  try { new Intl.DateTimeFormat("en", { timeZone: schedule.timezone }); }
  catch { throw new Error("The backup service returned an invalid schedule timezone."); }
  return schedule as BackupSchedule;
}

function decodeBackupStorage(value: unknown): BackupStorage {
  const storage = objectValue(value);
  const bytes = (value: unknown) => value === null || Number.isSafeInteger(value) && (value as number) >= 0;
  if (!storage || typeof storage.path !== "string" || !storage.path || !bytes(storage.total_bytes)
    || !bytes(storage.free_bytes) || !(storage.error === null || typeof storage.error === "string")
    || storage.total_bytes !== null && storage.free_bytes !== null && Number(storage.free_bytes) > Number(storage.total_bytes)) {
    throw new Error("The backup service returned invalid storage information.");
  }
  return storage as BackupStorage;
}

export function backupResponseMessage(value: unknown, fallback: string): string {
  const result = objectValue(value);
  const detail = result?.detail ?? result?.error;
  if (typeof detail === "string") return detail;
  const error = objectValue(detail);
  return typeof error?.message === "string" ? error.message : fallback;
}

export async function backupJson(path: string, init: RequestInit) {
  const response = await fetch(path, { ...init, cache: "no-store" });
  const value = await readBoundedJson(response, 4 * 1024 * 1024);
  if (!response.ok) throw new Error(backupResponseMessage(value, "The backup request failed."));
  return value;
}

export function backupMaximumBytes(configured?: string): number {
  if (configured === undefined) return BACKUP_DEFAULT_MAX_BYTES;
  const size = Number(configured);
  if (!Number.isSafeInteger(size) || size < 1) throw new Error("Invalid backup size configuration.");
  return size;
}

export function decodeBackup(value: unknown): ProjectBackup {
  const backup = objectValue(value);
  if (!backup || typeof backup.filename !== "string" || !BACKUP_FILENAME.test(backup.filename)
    || typeof backup.created_at !== "string" || !validUtcDateTime(backup.created_at.replace(/\+00:00$/, "Z"))
    || typeof backup.size_bytes !== "number" || !Number.isSafeInteger(backup.size_bytes)
    || backup.size_bytes < 1) throw new Error("The backup service returned an invalid archive.");
  return backup as ProjectBackup;
}

export function decodeBackupJob(value: unknown, projectId: string, jobId?: string): BackupJob {
  const job = objectValue(value);
  if (!job || job.project_id !== projectId || !validUuid(job.job_id)
    || jobId !== undefined && job.job_id !== jobId
    || !["pending", "running", "succeeded", "failed"].includes(String(job.state))) {
    throw new Error("The backup service returned an invalid job.");
  }
  if (job.state === "succeeded") decodeBackup(job.result);
  if (job.state === "failed") {
    const error = objectValue(job.error);
    if (!error || typeof error.code !== "string" || typeof error.message !== "string") {
      throw new Error("The backup service returned an invalid job failure.");
    }
  }
  return job as BackupJob;
}

export function decodeBackups(value: unknown, projectId: string): ProjectBackups {
  const result = objectValue(value);
  if (!result || result.project_id !== projectId || !Array.isArray(result.backups)) {
    throw new Error("The backup service returned an invalid project backup list.");
  }
  const backups = result.backups.map(decodeBackup);
  if (new Set(backups.map((backup) => backup.filename)).size !== backups.length) {
    throw new Error("The backup service returned duplicate archives.");
  }
  return { project_id: projectId, backups, schedule: decodeBackupSchedule(result.schedule),
    storage: decodeBackupStorage(result.storage), latest_job: result.latest_job === null ? null : decodeBackupJob(result.latest_job, projectId) };
}

export function backupAge(createdAt: string, now = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - Date.parse(createdAt)) / 60_000));
  if (minutes < 1) return "Less than a minute ago";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function backupSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
}

export const backupPath = (projectId: string) => `/api/backups/projects/${encodeURIComponent(projectId)}`;

const REJECTED_BACKUP_ERRORS: Readonly<Record<string, readonly number[]>> = {
  confirmation_required: [400], invalid_length: [400], invalid_archive: [400, 415],
  unauthorized: [401], project_not_found: [404], backup_not_found: [404],
  backup_busy: [409], backup_database_busy: [409], backup_dependency_conflict: [409],
  backup_integrity_conflict: [409], backup_operations_pending: [409],
  backup_project_mismatch: [409], backup_schema_mismatch: [409],
  backup_too_large: [413], invalid_backup: [422]
};

export function definitiveBackupFailure(status: number, value: unknown): boolean {
  const error = objectValue(objectValue(value)?.error);
  return Boolean(error && typeof error.code === "string" && typeof error.message === "string"
    && REJECTED_BACKUP_ERRORS[error.code]?.includes(status));
}
