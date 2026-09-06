import { exactKeys, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export function decimalString(value: unknown, positive = false): value is string {
  return typeof value === "string" && /^(0|[1-9][0-9]{0,18})$/.test(value)
    && BigInt(value) <= 9_223_372_036_854_775_807n && (!positive || value !== "0");
}
export function validOpaqueCursor(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > 512
    || !/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) return false;
  try {
    const bytes = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
    return btoa(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") === value;
  } catch { return false; }
}
export interface ActivityCursor {
  v: 1; kind: "activity"; project_id: string; stream_id: string; after: string;
}
export interface ReportsCursor {
  v: 1; kind: "reports"; project_id: string; stream_id: string;
  dismissal: "undismissed" | "dismissed" | "all"; work_item_id: string | null;
  upper: string; last: string;
}
export interface ProvenanceCursor {
  v: 1; kind: "report_follow_ups"; project_id: string; stream_id: string;
  report_id: string;
  upper: string; last: string;
}
export type Phase12Cursor = ActivityCursor | ReportsCursor | ProvenanceCursor;
export function decodePhase12Cursor(value: unknown, projectId: string, kind: Phase12Cursor["kind"]): Phase12Cursor {
  const invalid = () => new Error("Mnemonic returned an invalid feed cursor.");
  if (!["activity", "reports", "report_follow_ups"].includes(kind)) throw invalid();
  if (!validOpaqueCursor(value)) throw invalid();
  let decoded: unknown;
  let text: string;
  try {
    const binary = atob(value.replace(/-/g, "+").replace(/_/g, "/"));
    text = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(binary, (char) => char.charCodeAt(0)));
    decoded = JSON.parse(text);
  } catch { throw invalid(); }
  const cursor = objectValue(decoded);
  const common = ["v", "kind", "project_id", "stream_id"];
  const fields = kind === "activity" ? ["after"] : kind === "reports"
    ? ["dismissal", "work_item_id", "upper", "last"]
    : ["report_id", "upper", "last"];
  if (!cursor || !exactKeys(cursor, [...common, ...fields]) || cursor.v !== 1 || cursor.kind !== kind
    || !sameUuid(cursor.project_id, projectId) || !validUuid(cursor.stream_id)
    || cursor.project_id !== String(cursor.project_id).toLowerCase() || cursor.stream_id !== String(cursor.stream_id).toLowerCase()
    || text !== JSON.stringify(Object.fromEntries(Object.entries(cursor).sort(([left], [right]) => left.localeCompare(right))))) throw invalid();
  if (kind === "activity") {
    if (!decimalString(cursor.after)) throw invalid();
  } else {
    if (!decimalString(cursor.upper) || !decimalString(cursor.last, true)
      || BigInt(cursor.last) > BigInt(cursor.upper)) throw invalid();
    if (kind === "reports" && (!["undismissed", "dismissed", "all"].includes(String(cursor.dismissal))
      || !(cursor.work_item_id === null || validUuid(cursor.work_item_id)))) throw invalid();
    if (kind === "report_follow_ups" && !validUuid(cursor.report_id)) throw invalid();
  }
  return cursor as unknown as Phase12Cursor;
}
