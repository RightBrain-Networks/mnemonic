export function validDecisionReviewPriority(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 100;
}

export function parseDecisionReviewPriority(value: string): number | null {
  if (!/^\d{1,3}$/.test(value)) return null;
  const priority = Number(value);
  return validDecisionReviewPriority(priority) ? priority : null;
}
