import { finiteInteger, objectValue, sameUuid } from "./wire-guards.ts";

export type TranscriptWarning = {
  code: string; path: string | null; service: string; message: string; action: string;
  affected: number; retry_at: string | null; uid: number | null; gid: number | null;
  owner_uid: number | null; owner_gid: number | null; mode: string | null;
};
export type TranscriptHealth = {
  project_id: string; checked_at: string; worker_checked_at: string | null;
  warnings: TranscriptWarning[]; warnings_omitted: number; affected_transcripts: number;
  recheck_seconds: number;
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
    || !finiteInteger(row.recheck_seconds, 1)) throw new Error("Invalid transcript health response.");
  return row as unknown as TranscriptHealth;
}
