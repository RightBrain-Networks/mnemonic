import type { HierarchyPresentation, HierarchySummary, Page } from "@/lib/types";
import { decodeWorkSummary } from "./human-gates.ts";
import {
  exactKeys,
  finiteInteger,
  objectValue,
  validUtcDateTime
} from "./wire-guards.ts";

export type HierarchyBranchTotal = {
  key: string;
  label: string;
  needsAttention?: boolean;
};

export const hierarchyOverlapNote =
  "Blocked, active, completed, discovered, and merged-duplicate counts can overlap.";

const PRESENTATION_FIELDS = [
  "direct_child_count",
  "descendant_count",
  "blocked_descendant_count",
  "active_descendant_count",
  "completed_descendant_count",
  "discovered_descendant_count",
  "branch_unresolved_human_gate_count",
  "branch_merged_duplicate_count",
  "is_discovered_work",
  "discovered_from_parent",
  "next_active_descendant_lease_expires_at"
] as const;
const HIERARCHY_FIELDS = [
  "summary", "self_matches_filter", "has_matching_descendants", "presentation"
] as const;
const PAGE_FIELDS = ["items", "total", "limit", "offset"] as const;

export const HIERARCHY_DECODER_FIELDS = {
  decodeHierarchyPage: PAGE_FIELDS,
  "decodeHierarchyPage:item": HIERARCHY_FIELDS,
  decodeHierarchyPresentation: PRESENTATION_FIELDS
} as const;

export function decodeHierarchyPresentation(value: unknown): HierarchyPresentation {
  const facts = objectValue(value);
  const countFields = PRESENTATION_FIELDS.slice(0, 8);
  if (
    !facts
    || !exactKeys(facts, PRESENTATION_FIELDS)
    || countFields.some((key) => !finiteInteger(facts[key]))
    || Number(facts.direct_child_count) > Number(facts.descendant_count)
    || Number(facts.blocked_descendant_count) > Number(facts.descendant_count)
    || Number(facts.active_descendant_count) > Number(facts.descendant_count)
    || Number(facts.completed_descendant_count) > Number(facts.descendant_count)
    || Number(facts.discovered_descendant_count) > Number(facts.descendant_count)
    || typeof facts.is_discovered_work !== "boolean"
    || typeof facts.discovered_from_parent !== "boolean"
    || facts.discovered_from_parent && !facts.is_discovered_work
    || !(facts.next_active_descendant_lease_expires_at === null
      || validUtcDateTime(facts.next_active_descendant_lease_expires_at))
  ) throw new Error("Mnemonic returned an invalid hierarchy presentation.");
  return facts as unknown as HierarchyPresentation;
}

export function decodeHierarchyPage(
  value: unknown,
  projectId: string,
  expectedLimit?: number,
  expectedOffset?: number
): Page<HierarchySummary> {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, PAGE_FIELDS)
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 1, 100)
    || !finiteInteger(page.offset)
    || expectedLimit !== undefined && page.limit !== expectedLimit
    || expectedOffset !== undefined && page.offset !== expectedOffset
    || page.items.length > Number(page.limit)
    || page.items.length > Number(page.total)
    || page.items.length > 0 && Number(page.offset) + page.items.length > Number(page.total)
  ) throw new Error("Mnemonic returned an invalid hierarchy page.");
  const items = page.items.map((entry) => {
    const item = objectValue(entry);
    if (
      !item
      || !exactKeys(item, HIERARCHY_FIELDS)
      || typeof item.self_matches_filter !== "boolean"
      || typeof item.has_matching_descendants !== "boolean"
    ) throw new Error("Mnemonic returned an invalid hierarchy item.");
    const summary = decodeWorkSummary(item.summary, projectId);
    if (summary.readiness.is_duplicate) {
      throw new Error("Mnemonic returned a duplicate in canonical hierarchy.");
    }
    return {
      summary,
      self_matches_filter: item.self_matches_filter,
      has_matching_descendants: item.has_matching_descendants,
      presentation: decodeHierarchyPresentation(item.presentation)
    };
  });
  const itemIds = items.map((item) => item.summary.work_item.id.toLowerCase());
  if (new Set(itemIds).size !== itemIds.length) {
    throw new Error("Mnemonic returned repeated hierarchy roots.");
  }
  return { items, total: page.total, limit: page.limit, offset: page.offset };
}

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function discoveryLabel(
  facts: Pick<HierarchyPresentation, "is_discovered_work" | "discovered_from_parent">,
  depth: number
): string | null {
  if (!facts.is_discovered_work) return depth > 0 ? "Planned child" : null;
  if (depth === 0) return "Discovered work · ungrouped";
  return facts.discovered_from_parent
    ? "Discovered sub-work"
    : "Discovered elsewhere · grouped here";
}

export function hierarchyBranchTotals(
  facts: Pick<
    HierarchyPresentation,
    | "direct_child_count"
    | "descendant_count"
    | "blocked_descendant_count"
    | "active_descendant_count"
    | "completed_descendant_count"
    | "discovered_descendant_count"
    | "branch_unresolved_human_gate_count"
    | "branch_merged_duplicate_count"
  >
): HierarchyBranchTotal[] {
  const totals: HierarchyBranchTotal[] = [
    {
      key: "direct-children",
      label: plural(facts.direct_child_count, "direct child", "direct children")
    },
    { key: "descendants", label: plural(facts.descendant_count, "descendant") }
  ];
  const optionalTotals: Array<[string, number, string]> = [
    ["blocked", facts.blocked_descendant_count, "blocked descendant"],
    ["active", facts.active_descendant_count, "active descendant"],
    ["completed", facts.completed_descendant_count, "completed descendant"],
    ["discovered", facts.discovered_descendant_count, "discovered descendant"],
    ["merged-duplicates", facts.branch_merged_duplicate_count, "merged duplicate audit record"]
  ];
  for (const [key, count, noun] of optionalTotals) {
    totals.push({ key, label: plural(count, noun) });
  }
  const count = facts.branch_unresolved_human_gate_count;
  totals.push({
    key: "human-attention",
    label: `${plural(count, "unresolved human question")} ${count === 1 ? "needs" : "need"} attention`,
    needsAttention: count > 0
  });
  return totals;
}
