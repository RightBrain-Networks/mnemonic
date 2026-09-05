import type { LeasePublic, Readiness, WorkStatus } from "./types.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  objectValue,
  sameUuid,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

const DISPLAY_STATES = new Set([
  "pending", "deferred", "done", "wont-do", "promoted",
  "active",
  "dropped",
  "blocked",
  "waiting",
  "duplicate"
]);

const LEASE_FIELDS = [
  "holder_client", "holder_session_id", "acquired_at", "renewed_at", "expires_at"
] as const;
const READINESS_FIELDS = [
  "lifecycle_status", "is_duplicate", "canonical_work_item_id", "is_terminal",
  "has_active_lease", "has_dropped_lease",
  "active_lease", "unresolved_blocker_count", "is_blocked", "unresolved_gate_count",
  "is_gated", "is_ready", "display_state"
] as const;

export const READINESS_DECODER_FIELDS = {
  decodeLease: LEASE_FIELDS,
  decodeReadiness: READINESS_FIELDS
} as const;

function decodeLease(value: unknown): LeasePublic | null {
  if (value === null) return null;
  const lease = objectValue(value);
  if (
    !lease
    || !exactKeys(lease, LEASE_FIELDS)
    || !boundedText(lease.holder_client, 80)
    || !boundedText(lease.holder_session_id, 200)
    || !validUtcDateTime(lease.acquired_at)
    || !validUtcDateTime(lease.renewed_at)
    || !validUtcDateTime(lease.expires_at)
  ) throw new Error("Mnemonic returned an invalid attention lease.");
  return lease as unknown as LeasePublic;
}

export function decodeReadiness(
  value: unknown,
  status: WorkStatus,
  workItemId: string
): Readiness {
  const readiness = objectValue(value);
  if (
    !readiness
    || !exactKeys(readiness, READINESS_FIELDS)
    || readiness.lifecycle_status !== status
    || typeof readiness.is_duplicate !== "boolean"
    || !validUuid(readiness.canonical_work_item_id)
    || (readiness.is_duplicate
      ? sameUuid(readiness.canonical_work_item_id, workItemId)
      : !sameUuid(readiness.canonical_work_item_id, workItemId))
    || typeof readiness.is_terminal !== "boolean"
    || typeof readiness.has_active_lease !== "boolean"
    || typeof readiness.has_dropped_lease !== "boolean"
    || !finiteInteger(readiness.unresolved_blocker_count)
    || typeof readiness.is_blocked !== "boolean"
    || !finiteInteger(readiness.unresolved_gate_count)
    || typeof readiness.is_gated !== "boolean"
    || typeof readiness.is_ready !== "boolean"
    || typeof readiness.display_state !== "string"
    || !DISPLAY_STATES.has(readiness.display_state)
  ) throw new Error("Mnemonic returned invalid attention readiness.");
  const lease = decodeLease(readiness.active_lease);
  const terminal = ["done", "wont-do", "promoted"].includes(status);
  const ready = status === "pending"
    && !readiness.is_duplicate
    && lease === null
    && readiness.unresolved_blocker_count === 0
    && readiness.unresolved_gate_count === 0;
  const displayState = readiness.is_duplicate
    ? "duplicate"
    : status !== "pending"
    ? status
    : readiness.unresolved_gate_count > 0
      ? "waiting"
      : readiness.unresolved_blocker_count > 0
        ? "blocked"
        : lease !== null
          ? "active"
          : readiness.has_dropped_lease
            ? "dropped"
            : "pending";
  if (
    readiness.is_terminal !== terminal
    || readiness.has_active_lease !== (lease !== null)
    || readiness.has_active_lease && readiness.has_dropped_lease
    || readiness.is_blocked !== (readiness.unresolved_blocker_count > 0)
    || readiness.is_gated !== (readiness.unresolved_gate_count > 0)
    || readiness.is_ready !== ready
    || readiness.display_state !== displayState
  ) throw new Error("Mnemonic returned incoherent attention readiness.");
  return { ...readiness, active_lease: lease } as unknown as Readiness;
}
