import type { HierarchyPresentation } from "@/lib/types";

export type HierarchyBranchTotal = {
  key: string;
  label: string;
  needsAttention?: boolean;
};

export const hierarchyOverlapNote =
  "Blocked, active, completed, and discovered descendant counts can overlap.";

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
    ["discovered", facts.discovered_descendant_count, "discovered descendant"]
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
