import type { ExternalCandidate, ExternalReference } from "./types.ts";
import { exactKeys, jsonEqual, objectValue, validUnicode, validUtcDateTime } from "./wire-guards.ts";

export const EXTERNAL_REFERENCE_FIELDS = ["url", "kind", "label", "state", "state_observed_at"] as const;
export const EXTERNAL_CANDIDATE_FIELDS = ["url", "title", "body", "state"] as const;
export const EXTERNAL_REFERENCE_DECODER_FIELDS = {
  decodeExternalReference: EXTERNAL_REFERENCE_FIELDS
} as const;
const STATES = new Set(["open", "closed", "merged", "unknown"]);
const LABEL_CONTROLS = /[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]/u;

// Python/PostgreSQL Unicode whitespace: U+FEFF is a format character, not whitespace.
function boundedExternalText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && validUnicode(value)
    && Array.from(value).length <= maximum
    && /[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/u.test(value);
}

/** Validate the supplied spelling before a URL parser can repair or normalize it. */
export function validExternalUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > 2_000
    || !/^[\x21-\x7e]+$/.test(value) || /%(?![A-Fa-f0-9]{2})/.test(value)) return false;
  const match = /^(https?):\/\/(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?)(?::([0-9]+))?((?:\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*)?)(?:\?[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?(?:#[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?$/i.exec(value);
  if (!match || match[3] !== undefined && Number(match[3]) > 65535) return false;
  const hostname = match[2]!;
  if (hostname.startsWith("[")) {
    // Only IPv6 syntax is delegated after excluding URL repair, zones and credentials.
    try { return new URL(`http://${hostname}/`).hostname.startsWith("["); } catch { return false; }
  }
  if (/^[0-9.]+$/.test(hostname)) {
    const octets = hostname.split(".");
    return octets.length === 4 && octets.every((octet) => /^(0|[1-9][0-9]{0,2})$/.test(octet) && Number(octet) <= 255);
  }
  const host = hostname.replace(/\.$/, "");
  return host.length <= 253 && host.split(".").every((label) => label.length >= 1
    && label.length <= 63 && /^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$/.test(label));
}

export function normalizeExternalObservation(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match || !validUtcDateTime(`${match[1]}${match[2] ?? ""}Z`)) return null;
  const zone = match[3]!;
  if (zone !== "Z" && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59)) return null;
  const date = new Date(`${match[1]}${zone}`);
  if (!Number.isFinite(date.getTime()) || date.getUTCFullYear() < 1 || date.getUTCFullYear() > 9999) return null;
  const fraction = (match[2]?.slice(1) ?? "").replace(/0+$/, "");
  return date.toISOString().slice(0, 19) + (Number(fraction) ? `.${fraction}` : "") + "Z";
}

export function validExternalReference(value: unknown, canonical = false): value is ExternalReference {
  const item = objectValue(value);
  if (!item || !exactKeys(item, EXTERNAL_REFERENCE_FIELDS.filter((key) =>
    key !== "label" && key !== "state_observed_at" || Object.hasOwn(item, key)))
    || !validExternalUrl(item.url) || typeof item.kind !== "string" || !["tracked-by", "references"].includes(item.kind)
    || typeof item.state !== "string" || !STATES.has(item.state)) return false;
  if (Object.hasOwn(item, "label") && (!boundedExternalText(item.label, 120)
    || LABEL_CONTROLS.test(item.label) || new TextEncoder().encode(item.label).length > 480)) return false;
  if (Object.hasOwn(item, "state_observed_at")) {
    const normalized = normalizeExternalObservation(item.state_observed_at);
    if (normalized === null || canonical && normalized !== item.state_observed_at) return false;
  }
  return true;
}

export function validExternalReferences(value: unknown, canonical = false): value is ExternalReference[] {
  return Array.isArray(value) && value.length <= 10
    && value.every((item) => validExternalReference(item, canonical))
    && new Set(value.map((item) => item.url)).size === value.length
    && new TextEncoder().encode(JSON.stringify(value)).length <= 32_768;
}

export function validSparseReferences(value: Record<string, unknown>): boolean {
  return !Object.hasOwn(value, "external_references") ||
    validExternalReferences(value.external_references, true) && value.external_references.length > 0;
}

export function referenceKeys(value: Record<string, unknown>, required: readonly string[]): string[] {
  return [...required, ...(Object.hasOwn(value, "external_references") ? ["external_references"] : [])];
}

export function normalizeExternalReferences(value: unknown): ExternalReference[] {
  if (!validExternalReferences(value)) throw new Error("External references contain an invalid URL, label, observation time, or duplicate URL.");
  return value.map((item) => ({ ...item, ...(item.state_observed_at === undefined ? {} : {
    state_observed_at: normalizeExternalObservation(item.state_observed_at)!
  }) }));
}

export function sameExternalReferences(left: unknown, right: unknown): boolean {
  try { return jsonEqual(normalizeExternalReferences(left ?? []), normalizeExternalReferences(right ?? [])); }
  catch { return false; }
}

export function validExternalCandidates(value: unknown): value is ExternalCandidate[] {
  return Array.isArray(value) && value.length <= 64 && value.every((entry) => {
    const candidate = objectValue(entry);
    return candidate !== null && exactKeys(candidate, EXTERNAL_CANDIDATE_FIELDS)
      && validExternalUrl(candidate.url) && boundedExternalText(candidate.title, 500)
      && !LABEL_CONTROLS.test(candidate.title)
      && typeof candidate.body === "string" && validUnicode(candidate.body)
      && !candidate.body.includes("\0") && Array.from(candidate.body).length <= 20_000
      && typeof candidate.state === "string" && STATES.has(candidate.state);
  }) && new Set(value.map((entry) => entry.url)).size === value.length;
}
