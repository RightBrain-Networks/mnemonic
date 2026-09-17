import { boundedText, exactKeys, objectValue, sameUuid, validUuid } from "./wire-guards.ts";
export const WORK_FIELDS = ["title", "summary", "tags", "checkpoint", "identifiers", "provenance"] as const;
export type WorkField = typeof WORK_FIELDS[number];
export type QueryMode = "terms" | "phrase" | "literal";
export type WorkExcerpt = { field: WorkField; text: string; matched_member_id: string; checkpoint_id: string | null; match_type: "lexical" | "substring" | "phrase" | "literal" };
export type WorkEvidence = { evidence_mode: "lexical" | "semantic" | "browse"; matched_fields: WorkField[]; excerpts: WorkExcerpt[]; excerpts_truncated: boolean };
export const EVIDENCE_FIELDS = ["evidence_mode", "matched_fields", "excerpts", "excerpts_truncated"] as const;
export function validQueryMode(value: unknown): value is QueryMode { return ["terms", "phrase", "literal"].includes(String(value)); }
export function validWorkFields(value: unknown): value is WorkField[] {
  return Array.isArray(value) && value.length > 0 && value.length <= WORK_FIELDS.length && JSON.stringify(value) === JSON.stringify(WORK_FIELDS.filter((field) => value.includes(field)));
}
export function decodeWorkEvidence(value: unknown, memberId: string, expected: WorkEvidence["evidence_mode"], fields: readonly string[] = WORK_FIELDS): WorkEvidence {
  const row = objectValue(value);
  const fail = () => { throw new Error("Mnemonic returned invalid supporting search evidence."); };
  if (!row || !(row.evidence_mode === expected || expected === "semantic" && row.evidence_mode === "lexical") || typeof row.excerpts_truncated !== "boolean"
    || !Array.isArray(row.matched_fields) || row.matched_fields.length > 6 || new Set(row.matched_fields).size !== row.matched_fields.length
    || row.matched_fields.some((field) => !fields.includes(field as string))
    || JSON.stringify(row.matched_fields) !== JSON.stringify(WORK_FIELDS.filter((field) => (row.matched_fields as unknown[]).includes(field)))
    || !Array.isArray(row.excerpts) || row.excerpts.length > 3) return fail();
  let length = 0;
  const checkpoints = new Set<string | null>();
  const excerpts = row.excerpts.map((value) => {
    const excerpt = objectValue(value);
    if (!excerpt || !exactKeys(excerpt, ["field", "text", "matched_member_id", "checkpoint_id", "match_type"])
      || !(row.matched_fields as unknown[]).includes(excerpt.field) || !boundedText(excerpt.text, 320)
      || !sameUuid(excerpt.matched_member_id, memberId) || !(excerpt.checkpoint_id === null || validUuid(excerpt.checkpoint_id))
      || !["lexical", "substring", "phrase", "literal"].includes(String(excerpt.match_type))) return fail();
    if (["title", "summary"].includes(String(excerpt.field)) && excerpt.checkpoint_id !== null || ["tags", "checkpoint", "provenance"].includes(String(excerpt.field)) && excerpt.checkpoint_id === null) return fail();
    checkpoints.add(excerpt.checkpoint_id as string | null);
    length += Array.from(excerpt.text as string).length;
    return excerpt as unknown as WorkExcerpt;
  });
  if (checkpoints.size > 1 || length > 320 || row.evidence_mode !== "lexical" && (row.matched_fields.length || excerpts.length || row.excerpts_truncated)) return fail();
  return { evidence_mode: row.evidence_mode as WorkEvidence["evidence_mode"], matched_fields: row.matched_fields as WorkField[], excerpts, excerpts_truncated: row.excerpts_truncated };
}
