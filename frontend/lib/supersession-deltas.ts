import { decisionIdPattern, referencePattern, type SupersessionDetail } from "./knowledge.ts";
import { boundedText, exactKeys, finiteInteger, objectValue, validUnicode } from "./wire-guards.ts";

export type SupersedesSection = {
  found: boolean; section_count: number; text?: string | null; truncated: boolean;
  source_characters_scanned: number;
};
export type SupersessionDelta = {
  source: "read-projection"; target_reference: string; before_reference: string | null;
  after_reference: string; kind: "new-supersession" | "continued-supersession";
  details_before: SupersessionDetail | null; details_after: SupersessionDetail | null;
  metadata_changed: boolean; section_before: SupersedesSection; section_after: SupersedesSection;
  unified_diff?: string | null; truncated: boolean; section_scan_limit: 65536;
  section_byte_limit: 8192; diff_byte_limit: 16384;
  interpretation: "Supersedes headings are evidence aids; read exact revisions to establish rule scope.";
};
function invalid(): never { throw new Error("Mnemonic returned invalid supersession comparison data."); }
function reference(value: unknown): value is string { return typeof value === "string" && referencePattern.test(value); }
function optionalProse(value: unknown, limit: number) {
  return value == null || typeof value === "string" && validUnicode(value) && !value.includes("\0")
    && new TextEncoder().encode(value).length <= limit;
}
function section(value: unknown): SupersedesSection {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ["found", "section_count", "truncated", "source_characters_scanned",
    ...(Object.hasOwn(item, "text") ? ["text"] : [])]) || typeof item.found !== "boolean"
    || !finiteInteger(item.section_count) || item.found !== (item.section_count > 0)
    || typeof item.truncated !== "boolean" || !finiteInteger(item.source_characters_scanned, 0, 65536)
    || !optionalProse(item.text, 8192) || !item.found && item.text != null && item.text !== "") invalid();
  return item as SupersedesSection;
}
function detail(value: unknown, target: string): SupersessionDetail | null {
  if (value === null) return null;
  const item = objectValue(value);
  if (!item || !exactKeys(item, ["decision_id", "revision", "extent", "reason"])
    || typeof item.decision_id !== "string" || !decisionIdPattern.test(item.decision_id)
    || item.decision_id !== target || !finiteInteger(item.revision, 0, 2147483647)
    || !(item.extent === "full" || item.extent === "partial") || !boundedText(item.reason, 4000)) invalid();
  return item as SupersessionDetail;
}
function sameDetail(left: SupersessionDetail | null, right: SupersessionDetail | null) {
  return left === null || right === null ? left === right : left.decision_id === right.decision_id
    && left.revision === right.revision && left.extent === right.extent && left.reason === right.reason;
}
export function decodeSupersessionDelta(value: unknown, target: string, after: string, trigger: string): SupersessionDelta {
  const item = objectValue(value);
  if (!item || !exactKeys(item, ["source", "target_reference", "before_reference", "after_reference", "kind",
    "details_before", "details_after", "metadata_changed", "section_before", "section_after", "truncated",
    "section_scan_limit", "section_byte_limit", "diff_byte_limit", "interpretation",
    ...(Object.hasOwn(item, "unified_diff") ? ["unified_diff"] : [])]) || trigger !== "supersession"
    || item.source !== "read-projection" || item.target_reference !== target || item.after_reference !== after
    || !reference(item.target_reference) || !reference(item.after_reference)
    || !["new-supersession", "continued-supersession"].includes(String(item.kind))
    || typeof item.metadata_changed !== "boolean" || typeof item.truncated !== "boolean"
    || item.section_scan_limit !== 65536 || item.section_byte_limit !== 8192 || item.diff_byte_limit !== 16384
    || item.interpretation !== "Supersedes headings are evidence aids; read exact revisions to establish rule scope."
    || !optionalProse(item.unified_diff, 16384)) invalid();
  const split = after.lastIndexOf("-"), number = Number(after.slice(split + 1));
  const expectedBefore = number > 0 ? `${after.slice(0, split)}-${number - 1}` : null;
  if (item.before_reference !== expectedBefore || item.kind === "continued-supersession" && expectedBefore === null) invalid();
  const targetId = target.slice(0, target.lastIndexOf("-"));
  const beforeDetail = detail(item.details_before, targetId), afterDetail = detail(item.details_after, targetId);
  const beforeSection = section(item.section_before), afterSection = section(item.section_after);
  if (afterDetail !== null && `${afterDetail.decision_id}-${afterDetail.revision}` !== target
    || item.metadata_changed !== (item.kind === "new-supersession" || !sameDetail(beforeDetail, afterDetail))
    || (beforeSection.truncated || afterSection.truncated) && !item.truncated) invalid();
  return { ...item, details_before: beforeDetail, details_after: afterDetail,
    section_before: beforeSection, section_after: afterSection } as SupersessionDelta;
}
