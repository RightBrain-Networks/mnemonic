import type {
  AdjacentRelationshipRead,
  RelationshipDirection,
  RelationshipType
} from "@/lib/types";

export const MAX_HIERARCHY_DEPTH = 50;

export function hierarchyGuardReason(
  workItemId: string,
  visited: ReadonlySet<string>,
  depth: number
): string | null {
  if (visited.has(workItemId)) {
    return "A restored relationship cycle reaches this work item again.";
  }
  if (depth >= MAX_HIERARCHY_DEPTH) {
    return "This branch exceeds the dashboard’s bounded hierarchy depth.";
  }
  return null;
}

export const relationshipTypeLabels: Record<RelationshipType, string> = {
  blocks: "Blocks",
  "parent-child": "Parent / child",
  "discovered-from": "Discovered from",
  "duplicate-of": "Duplicate of",
  related: "Related"
};

export function relationshipGroup(relationship: AdjacentRelationshipRead): string {
  const type = relationship.relationship.relationship_type;
  if (type === "blocks") return relationship.direction === "outgoing" ? "Blocks" : "Blocked by";
  if (type === "parent-child") return relationship.direction === "outgoing" ? "Children" : "Parent";
  if (type === "discovered-from") {
    return relationship.direction === "outgoing" ? "Discovered from" : "Discovered work";
  }
  if (type === "duplicate-of") return "Duplicate of";
  return "Related";
}

export function relationshipPreview(
  type: RelationshipType,
  direction: Exclude<RelationshipDirection, "undirected">,
  currentTitle: string,
  counterpartTitle: string
): string {
  const source = direction === "outgoing" ? currentTitle : counterpartTitle;
  const target = direction === "outgoing" ? counterpartTitle : currentTitle;
  if (type === "blocks") return `${source} blocks ${target}.`;
  if (type === "parent-child") return `${source} is the parent of ${target}.`;
  if (type === "discovered-from") return `${source} was discovered from ${target}.`;
  if (type === "duplicate-of") return `${source} is a duplicate of ${target}.`;
  return `${currentTitle} and ${counterpartTitle} are related.`;
}

export function relationshipConflictMessage(code?: string): string | null {
  if (code === "relationship_cycle") {
    return "That edge would create a cycle. Reverse the direction or choose another work item.";
  }
  if (code === "parent_already_set" || code === "parent_already_exists" || code === "relationship_parent_conflict") {
    return "That child already has a parent. Remove its existing parent relationship first.";
  }
  return null;
}
