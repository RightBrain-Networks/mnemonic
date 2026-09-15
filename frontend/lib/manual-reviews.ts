import { boundedText, exactKeys, finiteInteger, objectValue, validUtcDateTime, validUuid } from "./wire-guards.ts";

export interface ManualReviewRequest {
  id: string;
  actor_client: "dashboard";
  actor_session_id: string;
  actor_model: null;
  created_at: string;
  event_id: string;
  work_version: number;
  priority: number;
}

export function decodeManualReviewRequest(value: unknown): ManualReviewRequest {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["id", "actor_client", "actor_session_id", "actor_model", "created_at", "event_id", "work_version", "priority"])
    || !validUuid(row.id) || row.actor_client !== "dashboard" || row.actor_model !== null
    || !boundedText(row.actor_session_id, 200) || !validUtcDateTime(row.created_at)
    || typeof row.event_id !== "string" || !/^[1-9][0-9]*$/.test(row.event_id)
    || BigInt(row.event_id) > 9223372036854775807n
    || !finiteInteger(row.work_version, 1) || !finiteInteger(row.priority, 0, 100)) {
    throw new Error("Mnemonic returned an invalid human review request.");
  }
  return row as unknown as ManualReviewRequest;
}
