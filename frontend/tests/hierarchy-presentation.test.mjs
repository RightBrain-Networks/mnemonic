import assert from "node:assert/strict";
import test from "node:test";
import {
  decodeHierarchyPresentation,
  discoveryLabel,
  hierarchyBranchTotals,
  hierarchyOverlapNote
} from "../lib/hierarchy-presentation.ts";

const totals = {
  direct_child_count: 1,
  descendant_count: 7,
  blocked_descendant_count: 1,
  active_descendant_count: 2,
  completed_descendant_count: 3,
  discovered_descendant_count: 1,
  branch_unresolved_human_gate_count: 1,
  branch_merged_duplicate_count: 4
};

test("discovery labels distinguish ungrouped, direct, grouped, and planned work", () => {
  assert.equal(discoveryLabel({ is_discovered_work: true, discovered_from_parent: false }, 0), "Discovered work · ungrouped");
  assert.equal(discoveryLabel({ is_discovered_work: true, discovered_from_parent: true }, 1), "Discovered sub-work");
  assert.equal(discoveryLabel({ is_discovered_work: true, discovered_from_parent: false }, 2), "Discovered elsewhere · grouped here");
  assert.equal(discoveryLabel({ is_discovered_work: false, discovered_from_parent: false }, 1), "Planned child");
  assert.equal(discoveryLabel({ is_discovered_work: false, discovered_from_parent: false }, 0), null);
});

test("branch totals spell out every descendant population with correct grammar", () => {
  assert.deepEqual(hierarchyBranchTotals(totals), [
    { key: "direct-children", label: "1 direct child" },
    { key: "descendants", label: "7 descendants" },
    { key: "blocked", label: "1 blocked descendant" },
    { key: "active", label: "2 active descendants" },
    { key: "completed", label: "3 completed descendants" },
    { key: "discovered", label: "1 discovered descendant" },
    { key: "merged-duplicates", label: "4 merged duplicate audit records" },
    {
      key: "human-attention",
      label: "1 unresolved human question needs attention",
      needsAttention: true
    }
  ]);
  assert.equal(hierarchyOverlapNote, "Blocked, active, completed, discovered, and merged-duplicate counts can overlap.");
});

test("zero totals remain exposed and plural questions use the plural verb", () => {
  assert.deepEqual(hierarchyBranchTotals({
    ...totals,
    direct_child_count: 0,
    descendant_count: 0,
    blocked_descendant_count: 0,
    active_descendant_count: 0,
    completed_descendant_count: 0,
    discovered_descendant_count: 0,
    branch_merged_duplicate_count: 0,
    branch_unresolved_human_gate_count: 2
  }), [
    { key: "direct-children", label: "0 direct children" },
    { key: "descendants", label: "0 descendants" },
    { key: "blocked", label: "0 blocked descendants" },
    { key: "active", label: "0 active descendants" },
    { key: "completed", label: "0 completed descendants" },
    { key: "discovered", label: "0 discovered descendants" },
    { key: "merged-duplicates", label: "0 merged duplicate audit records" },
    {
      key: "human-attention",
      label: "2 unresolved human questions need attention",
      needsAttention: true
    }
  ]);
});

test("parent discovery cannot be asserted for planned work", () => {
  const presentation = {
    ...totals,
    is_discovered_work: false,
    discovered_from_parent: true,
    next_active_descendant_lease_expires_at: null
  };
  assert.throws(
    () => decodeHierarchyPresentation(presentation),
    /invalid hierarchy presentation/
  );
});
