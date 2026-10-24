import { exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

export type RemediationTarget = "code" | "decision-record" | "both";
export type FindingSummary = { named_finding_count: number; unlabelled_change_required_count: number; change_required_row_count: number };
export const findingIdPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/;

export function validFindingMetadata(item: Record<string, unknown>): boolean {
  return (item.finding_id == null || typeof item.finding_id === "string" && item.finding_id.length <= 100 && item.finding_id.trim() === item.finding_id && findingIdPattern.test(item.finding_id))
    && (item.remediation_target == null || item.disposition === "change-required" && typeof item.remediation_target === "string" && ["code", "decision-record", "both"].includes(item.remediation_target));
}

export function validFindingSummary(value: unknown, rowCount?: unknown): value is FindingSummary {
  const summary = objectValue(value);
  if (!summary || !exactKeys(summary, ["named_finding_count", "unlabelled_change_required_count", "change_required_row_count"])
    || !finiteInteger(summary.named_finding_count) || !finiteInteger(summary.unlabelled_change_required_count)
    || !finiteInteger(summary.change_required_row_count) || rowCount !== undefined && rowCount !== summary.change_required_row_count) return false;
  const labelled = summary.change_required_row_count - summary.unlabelled_change_required_count;
  return summary.named_finding_count <= labelled && Boolean(summary.named_finding_count) === Boolean(labelled);
}

export function findingSummaryLabel(summary: FindingSummary): string {
  const named = `${summary.named_finding_count} named ${summary.named_finding_count === 1 ? "finding" : "findings"}`;
  const unknown = `${summary.unlabelled_change_required_count} unlabelled ${summary.unlabelled_change_required_count === 1 ? "row" : "rows"}`;
  return summary.unlabelled_change_required_count ? `${named} · ${unknown}` : named;
}
