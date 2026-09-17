import { boundedText, exactKeys, objectValue, sameUuid, type JsonObject } from "./wire-guards.ts";

// Dashboard views select one project. Multi-project MCP responses cannot be
// substituted for that scope, even when their currently returned rows agree.
export function validateSingleProjectCoverage(page: JsonObject, projectId: string): void {
  const rows = page.project_coverage;
  const row = Array.isArray(rows) && rows.length === 1 ? objectValue(rows[0]) : null;
  if (!row || !exactKeys(row, ["project_id", "project_name", "project_slug", "facet_totals", "coverage", "indexing_incomplete"])
    || !sameUuid(row.project_id, projectId) || !boundedText(row.project_name, 120) || !boundedText(row.project_slug, 100)
    || row.indexing_incomplete !== page.indexing_incomplete
    || !sameJson(row.facet_totals, page.facet_totals) || !sameJson(row.coverage, page.coverage)) {
    throw new Error("Mnemonic returned search coverage outside the requested project.");
  }
}

function sameJson(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  const first = objectValue(left), second = objectValue(right);
  return !!first && !!second && exactKeys(first, Object.keys(second))
    && Object.keys(first).every((key) => sameJson(first[key], second[key]));
}
