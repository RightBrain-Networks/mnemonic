import { decimalString } from "./activity-cursors.ts";
import { boundedText, exactKeys, objectValue, sameUuid } from "./wire-guards.ts";

export const PRIORITY_RUBRIC_MAX_CHARS = 100_000;
export const PRIORITY_RUBRIC_RESPONSE_BYTES = 2 * 1024 * 1024;
export type PriorityRubric = { project_id: string; content: string; revision: string };

export function validPriorityRubricContent(value: unknown): value is string {
  return boundedText(value, PRIORITY_RUBRIC_MAX_CHARS);
}

export function decodePriorityRubric(value: unknown, projectId: string): PriorityRubric {
  const rubric = objectValue(value);
  if (!rubric || !exactKeys(rubric, ["project_id", "content", "revision"])
    || !sameUuid(rubric.project_id, projectId) || !decimalString(rubric.revision, true)
    || !validPriorityRubricContent(rubric.content)) {
    throw new Error("Mnemonic returned an invalid priority rubric.");
  }
  return rubric as PriorityRubric;
}
