import { exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

export const SEARCH_DATE_FIELDS = ["created_after", "created_before", "updated_after", "updated_before"] as const;
export type SearchDateBounds = Partial<Record<typeof SEARCH_DATE_FIELDS[number], string | null>>;
export type DiagnosticsMode = "on_empty" | "always" | "off";
export type TagCountRequest = { limit?: number; offset?: number };
export type TagCountPage = { items: { tag: string; count: number }[]; total: number; limit: number; offset: number; next_offset: number | null; count_unit: "canonical_work_items"; member_scope: "returned_work_items"; selected_tag_applied: true };
export const validDiagnosticsMode = (value: unknown): value is DiagnosticsMode => ["on_empty", "always", "off"].includes(String(value));

export function searchDate(value: unknown): bigint | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return null;
  const date = value.slice(0, 10), time = value.slice(11, 19), [year, month, day] = date.split("-").map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const maximumDay = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > maximumDay || time.slice(0, 2) > "23" || time.slice(3, 5) > "59" || time.slice(6, 8) > "59") return null;
  const fraction = /\.(\d{1,6})/.exec(value)?.[1]?.padEnd(6, "0") ?? "000000";
  return BigInt(instant) * 1000n + BigInt(fraction.slice(3));
}
export function validDateBounds(value: Record<string, unknown>, utcOnly = false): boolean {
  for (const key of SEARCH_DATE_FIELDS) {
    const bound = value[key];
    if (bound == null) continue;
    if (searchDate(bound) === null || utcOnly && (typeof bound !== "string" || !/(?:Z|\+00:00)$/.test(bound))) return false;
  }
  return ["created", "updated"].every((prefix) => value[`${prefix}_after`] == null || value[`${prefix}_before`] == null
    || searchDate(value[`${prefix}_after`])! < searchDate(value[`${prefix}_before`])!);
}
export function dateBoundsMatch(actual: Record<string, unknown>, expected: Record<string, unknown>): boolean {
  return SEARCH_DATE_FIELDS.every((key) => expected[key] == null ? actual[key] === undefined : searchDate(actual[key]) === searchDate(expected[key]));
}
export function validTagCountRequest(value: unknown): value is TagCountRequest {
  const row = objectValue(value);
  return Boolean(row && Object.keys(row).every((key) => ["limit", "offset"].includes(key))
    && (row.limit === undefined || finiteInteger(row.limit, 1, 100)) && (row.offset === undefined || finiteInteger(row.offset, 0, 1_000_000)));
}
function codepointBefore(left: string, right: string): boolean {
  const a = Array.from(left, (char) => char.codePointAt(0)!), b = Array.from(right, (char) => char.codePointAt(0)!);
  for (let index = 0; index < Math.min(a.length, b.length); index++) {
    if (a[index] !== b[index]) return a[index] < b[index];
  }
  return a.length < b.length;
}
export function decodeTagCounts(value: unknown, request: TagCountRequest | null, workTotal: number): TagCountPage | null {
  if (request === null && value === null) return null;
  const row = objectValue(value);
  if (!request || !validTagCountRequest(request) || !row || !exactKeys(row, ["items", "total", "limit", "offset", "next_offset", "count_unit", "member_scope", "selected_tag_applied"])
    || !finiteInteger(row.total) || row.limit !== (request.limit ?? 50) || row.offset !== (request.offset ?? 0)
    || workTotal === 0 && row.total !== 0
    || !Array.isArray(row.items) || row.items.length !== Math.min(row.limit as number, Math.max(0, row.total - (row.offset as number)))
    || row.count_unit !== "canonical_work_items" || row.member_scope !== "returned_work_items" || row.selected_tag_applied !== true
    || row.next_offset !== ((row.offset as number) + row.items.length < row.total ? (row.offset as number) + row.items.length : null)) throw new Error("Mnemonic returned invalid tag counts.");
  let previous: string | null = null;
  for (const value of row.items) {
    const item = objectValue(value);
    if (!item || !exactKeys(item, ["tag", "count"]) || typeof item.tag !== "string" || !item.tag || Array.from(item.tag).length > 50
      || !finiteInteger(item.count, 1, workTotal) || previous !== null && !codepointBefore(previous, item.tag)) throw new Error("Mnemonic returned invalid tag counts.");
    previous = item.tag;
  }
  return row as unknown as TagCountPage;
}
