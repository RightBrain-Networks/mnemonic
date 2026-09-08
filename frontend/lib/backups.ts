import { objectValue, validUtcDateTime } from "./wire-guards.ts";

export const BACKUP_DEFAULT_MAX_BYTES = 67_108_864;
export const BACKUP_FILENAME = /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.bz2$/;

export type ProjectBackup = { filename: string; created_at: string; size_bytes: number };
export type ProjectBackups = {
  project_id: string;
  retention_count: number;
  backups: ProjectBackup[];
};

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

export function decodeBackups(value: unknown, projectId: string): ProjectBackups {
  const result = objectValue(value);
  if (!result || result.project_id !== projectId || !Number.isSafeInteger(result.retention_count)
    || (result.retention_count as number) < 1 || !Array.isArray(result.backups)) {
    throw new Error("The backup service returned an invalid project backup list.");
  }
  const backups = result.backups.map(decodeBackup);
  if (new Set(backups.map((backup) => backup.filename)).size !== backups.length) {
    throw new Error("The backup service returned duplicate archives.");
  }
  return { project_id: projectId, retention_count: result.retention_count as number, backups };
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
