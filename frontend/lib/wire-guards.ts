import type { WorkItem, WorkStatus } from "./types.ts";

export type JsonObject = Record<string, unknown>;

export const UUID_PATTERN = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;
export const UTC_DATE_TIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?Z$/;

const WORK_STATUSES = new Set<WorkStatus>([
  "pending", "deferred", "done", "wont-do", "promoted"
]);

export function objectValue(value: unknown): JsonObject | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null
    ? value as JsonObject
    : null;
}

export function exactKeys(value: JsonObject, keys: Iterable<string>): boolean {
  const expected = new Set(keys);
  const actual = Object.keys(value);
  return actual.length === expected.size && actual.every((key) => expected.has(key));
}

export function finiteInteger(
  value: unknown,
  minimum = 0,
  maximum?: number
): value is number {
  return Number.isSafeInteger(value)
    && Number(value) >= minimum
    && (maximum === undefined || Number(value) <= maximum);
}

export function validUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return false;
    }
  }
  return true;
}

export function boundedText(value: unknown, maximum: number): value is string {
  if (
    typeof value !== "string"
    || !validUnicode(value)
    || value.includes("\0")
    || value.trim().length === 0
  ) return false;
  return Array.from(value).length <= maximum;
}

export function nullableBoundedText(value: unknown, maximum: number): value is string | null {
  return value === null || boundedText(value, maximum);
}

export function validUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

export function nullableUuid(value: unknown): value is string | null {
  return value === null || validUuid(value);
}

export function sameUuid(left: unknown, right: unknown): boolean {
  return validUuid(left) && validUuid(right) && left.toLowerCase() === right.toLowerCase();
}

export function validUtcDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = UTC_DATE_TIME_PATTERN.exec(value);
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day >= 1 && day <= days[month - 1]!;
}

export function validBoundedMetadata(
  value: unknown,
  reservedKeys: ReadonlySet<string>,
  maximumBytes = 16_384
): value is JsonObject {
  const stack = new WeakSet<object>();
  let separatorBytes = 0;

  const visit = (item: unknown): boolean => {
    if (item === null || typeof item === "boolean") return true;
    if (typeof item === "number") return Number.isFinite(item);
    if (typeof item === "string") return validUnicode(item) && !item.includes("\0");
    if (typeof item !== "object" || stack.has(item)) return false;
    stack.add(item);
    if (Array.isArray(item)) {
      separatorBytes += Math.max(0, item.length - 1);
      const valid = item.every(visit);
      stack.delete(item);
      return valid;
    }
    const object = objectValue(item);
    if (!object) {
      stack.delete(item);
      return false;
    }
    const entries = Object.entries(object);
    separatorBytes += entries.length ? (2 * entries.length) - 1 : 0;
    const valid = entries.every(([key, entry]) => (
      validUnicode(key)
      && !key.includes("\0")
      && !reservedKeys.has(key.toLowerCase())
      && visit(entry)
    ));
    stack.delete(item);
    return valid;
  };

  if (!objectValue(value) || !visit(value)) return false;
  try {
    const encoded = JSON.stringify(value);
    return encoded !== undefined
      && new TextEncoder().encode(encoded).byteLength + separatorBytes <= maximumBytes;
  } catch {
    return false;
  }
}

export function decodeWorkItem(
  value: unknown,
  projectId: string,
  workItemId?: string,
  errorMessage = "Mnemonic returned an invalid mutation response."
): WorkItem {
  const item = objectValue(value);
  if (
    !item
    || !exactKeys(item, [
      "id", "project_id", "title", "summary", "status", "priority",
      "initial_checkpoint_id", "version", "created_at", "updated_at"
    ])
    || !validUuid(item.id)
    || !sameUuid(item.project_id, projectId)
    || (workItemId !== undefined && !sameUuid(item.id, workItemId))
    || !boundedText(item.title, 200)
    || !boundedText(item.summary, 1_000)
    || typeof item.status !== "string"
    || !WORK_STATUSES.has(item.status as WorkStatus)
    || !finiteInteger(item.priority, 0, 100)
    || !validUuid(item.initial_checkpoint_id)
    || !finiteInteger(item.version, 1)
    || !validUtcDateTime(item.created_at)
    || !validUtcDateTime(item.updated_at)
  ) throw new Error(errorMessage);
  return item as unknown as WorkItem;
}
