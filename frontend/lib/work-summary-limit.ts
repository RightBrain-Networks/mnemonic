export const DEFAULT_WORK_SUMMARY_MAX_CHARS = 2048;

export function workSummaryMaxChars(value: string | undefined): number {
  if (value === undefined) return DEFAULT_WORK_SUMMARY_MAX_CHARS;
  const maximum = Number(value);
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(maximum) || maximum < 1) {
    throw new Error("MNEMONIC_WORK_SUMMARY_MAX_CHARS must be a positive integer.");
  }
  return maximum;
}

export function workSummaryValidationMessage(value: string, maximum: number): string {
  return Array.from(value.trim()).length > maximum
    ? `Work summary exceeds the configured maximum of ${maximum} characters.`
    : "";
}
