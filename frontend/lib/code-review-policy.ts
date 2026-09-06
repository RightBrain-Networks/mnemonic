export interface CodeReviewSettings {
  code_review_required_min_priority: number;
  code_review_optional_min_priority: number;
  allow_remediation_code_reviews: boolean;
}

export type CodeReviewDecision =
  | "mandatory"
  | "ask_recommendation"
  | "not_requested"
  | "ineligible_depth_limit"
  | "ineligible_remediation_disabled";

export function validReviewThreshold(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 100 &&
    value % 5 === 0
  );
}

export function validReviewSettings(value: CodeReviewSettings): boolean {
  return (
    validReviewThreshold(value.code_review_required_min_priority) &&
    validReviewThreshold(value.code_review_optional_min_priority) &&
    typeof value.allow_remediation_code_reviews === "boolean"
  );
}

export function reviewThresholdLabel(value: number): string {
  if (!validReviewThreshold(value))
    throw new Error("Invalid code review threshold.");
  return value === 0
    ? "Always"
    : value === 100
      ? "Never"
      : `${value} and above`;
}

export function reviewThresholdMatches(
  priority: number,
  threshold: number,
): boolean {
  if (
    !Number.isInteger(priority) ||
    priority < 0 ||
    priority > 100 ||
    !validReviewThreshold(threshold)
  )
    throw new Error("Invalid code review policy.");
  return threshold === 0 || (threshold !== 100 && priority >= threshold);
}

export function codeReviewDecision(
  settings: CodeReviewSettings,
  priority: number,
  depth: number,
): CodeReviewDecision {
  if (
    !validReviewSettings(settings) ||
    ![0, 1, 2].includes(depth) ||
    !Number.isInteger(priority) ||
    priority < 0 ||
    priority > 100
  ) {
    throw new Error("Invalid code review policy.");
  }
  if (depth === 2) return "ineligible_depth_limit";
  if (depth === 1 && !settings.allow_remediation_code_reviews)
    return "ineligible_remediation_disabled";
  if (
    reviewThresholdMatches(priority, settings.code_review_required_min_priority)
  )
    return "mandatory";
  return reviewThresholdMatches(
    priority,
    settings.code_review_optional_min_priority,
  )
    ? "ask_recommendation"
    : "not_requested";
}

export function reviewPolicySummary(settings: CodeReviewSettings): string {
  const required = reviewThresholdLabel(
    settings.code_review_required_min_priority,
  );
  const optional = reviewThresholdLabel(
    settings.code_review_optional_min_priority,
  );
  return `Mandatory review: ${required.toLowerCase()}. Agent recommendation: ${optional.toLowerCase()}. Mandatory review takes precedence. ${
    settings.allow_remediation_code_reviews
      ? "First-generation remediation follows these thresholds."
      : "Remediation work does not request reviews."
  } Further remediation can never request a review.`;
}
