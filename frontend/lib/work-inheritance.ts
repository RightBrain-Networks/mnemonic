import type { WorkItem } from "./types.ts";
import { boundedText, exactKeys, objectValue, sameUuid, validUtcDateTime, validUuid } from "./wire-guards.ts";

export function validInheritanceExclusion(work: Record<string, unknown>): boolean {
  if (!Object.hasOwn(work, "decision_change_inheritance_exclusion")) return true;
  const exclusion = objectValue(work.decision_change_inheritance_exclusion);
  return !!exclusion
    && exactKeys(exclusion, ["origin_work_item_id", "origin_context_checkpoint_id", "reason", "created_at"])
    && validUuid(exclusion.origin_work_item_id)
    && !sameUuid(exclusion.origin_work_item_id, work.id)
    && validUuid(exclusion.origin_context_checkpoint_id)
    && boundedText(exclusion.reason, 4000)
    && validUtcDateTime(exclusion.created_at)
    && exclusion.created_at === work.created_at;
}

/** Bind new assertions to this immutable creation response, never current lineage. */
export function creationInheritanceInputs(
  body: Record<string, unknown>, creation: Record<string, unknown>, work: WorkItem
): unknown[] {
  const inputs: unknown[] = Array.isArray(body.initial_relationships) ? [...body.initial_relationships] : [];
  const origin = body.discovered_from_work_item_id;
  const edges = Array.isArray(creation.initial_relationships) ? creation.initial_relationships : [];
  const originEdge = edges.map(objectValue).find((edge) => edge
    && edge.relationship_type === "discovered-from" && sameUuid(edge.target_work_item_id, origin));
  const exclusion = work.decision_change_inheritance_exclusion;
  if (body.inherit_decision_change === false) {
    if (!validUuid(origin) || !exclusion || !sameUuid(exclusion.origin_work_item_id, origin)
      || exclusion.reason !== body.decision_change_inheritance_reason
      || !originEdge || !sameUuid(originEdge.context_checkpoint_id, exclusion.origin_context_checkpoint_id)) {
      throw new Error("Mnemonic returned an incoherent inheritance declaration.");
    }
  } else if (exclusion || body.decision_change_inheritance_reason !== undefined) {
    throw new Error("Mnemonic returned an unexpected inheritance declaration.");
  }
  if (validUuid(origin) && !inputs.some((input) => {
    const edge = objectValue(input);
    return edge?.type === "discovered-from" && sameUuid(edge.other_work_item_id, origin);
  })) {
    if (!originEdge || !sameUuid(originEdge.context_checkpoint_work_item_id, origin)
      || !validUuid(originEdge.context_checkpoint_id)) {
      throw new Error("Mnemonic returned an incoherent origin discovery edge.");
    }
    inputs.push({ type: "discovered-from", direction: "outgoing", other_work_item_id: origin,
      context_checkpoint_id: originEdge.context_checkpoint_id });
  }
  return inputs;
}
