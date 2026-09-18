import { finiteInteger, objectValue, sameUuid } from "./wire-guards.ts";

export type TranscriptWarning = {
  code: string; path: string | null; service: string; message: string; action: string;
  affected: number; retry_at: string | null; uid: number | null; gid: number | null;
  owner_uid: number | null; owner_gid: number | null; mode: string | null;
};
export type TranscriptHealth = {
  project_id: string; checked_at: string; worker_checked_at: string | null;
  warnings: TranscriptWarning[]; warnings_omitted: number; affected_transcripts: number;
  recheck_seconds: number; storage?: TranscriptStorageUsage | null;
};
const timestamp = (value: unknown) => typeof value === "string" && Number.isFinite(Date.parse(value));
const nullableNumber = (value: unknown) => value === null || finiteInteger(value, 0);
function validWarning(value: unknown): value is TranscriptWarning {
  const row = objectValue(value);
  return Boolean(row && typeof row.code === "string" && /^[a-z_]+$/.test(row.code)
    && (row.path === null || typeof row.path === "string" && row.path.length <= 4096)
    && ["api", "worker"].includes(String(row.service))
    && typeof row.message === "string" && typeof row.action === "string"
    && finiteInteger(row.affected, 0) && (row.retry_at === null || timestamp(row.retry_at))
    && [row.uid, row.gid, row.owner_uid, row.owner_gid].every(nullableNumber)
    && (row.mode === null || typeof row.mode === "string" && /^[0-7]{4}$/.test(row.mode)));
}
export function decodeTranscriptHealth(value: unknown, projectId: string): TranscriptHealth {
  const row = objectValue(value);
  if (!row || !sameUuid(row.project_id, projectId) || !timestamp(row.checked_at)
    || !(row.worker_checked_at === null || timestamp(row.worker_checked_at))
    || !Array.isArray(row.warnings) || row.warnings.length > 50 || !row.warnings.every(validWarning)
    || !finiteInteger(row.warnings_omitted, 0) || !finiteInteger(row.affected_transcripts, 0)
    || !finiteInteger(row.recheck_seconds, 1) || !validTranscriptStorage(row.storage)) throw new Error("Invalid transcript health response.");
  return row as unknown as TranscriptHealth;
}

export type TranscriptDirectoryUsage = {
  checked_at: string; bytes: number | null; logical_bytes: number | null;
  free_bytes: number | null; total_bytes: number | null; complete: boolean;
  error_code: string | null;
};
export type TranscriptStorageUsage = {
  scope: "all_projects"; transcripts: TranscriptDirectoryUsage | null;
  index: TranscriptDirectoryUsage | null; database_bytes: number;
};
function validDirectoryUsage(value: unknown): value is TranscriptDirectoryUsage | null {
  if (value === null) return true;
  const row = objectValue(value);
  return Boolean(row && timestamp(row.checked_at) && typeof row.complete === "boolean"
    && [row.bytes, row.logical_bytes, row.free_bytes, row.total_bytes].every(nullableNumber)
    && (row.error_code === null || typeof row.error_code === "string")
    && (!row.complete || row.bytes !== null && row.logical_bytes !== null));
}
export function validTranscriptStorage(value: unknown): value is TranscriptStorageUsage | null {
  if (value === null || value === undefined) return true;
  const row = objectValue(value);
  return Boolean(row && row.scope === "all_projects" && finiteInteger(row.database_bytes)
    && validDirectoryUsage(row.transcripts) && validDirectoryUsage(row.index));
}
export function transcriptWarningKey(warning: TranscriptWarning): string {
  return JSON.stringify([warning.service, warning.code, warning.path, warning.uid, warning.gid,
    warning.owner_uid, warning.owner_gid, warning.mode]);
}
export function readDismissedTranscriptWarnings(value: string | null): string[] {
  try {
    const parsed: unknown = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string" && item.length <= 5000).slice(-200) : [];
  } catch { return []; }
}
