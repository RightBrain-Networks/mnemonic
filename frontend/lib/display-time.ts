const DEFAULT_DISPLAY_TIME_ZONE = "UTC";

let displayTimeZone = DEFAULT_DISPLAY_TIME_ZONE;

function parseDate(value: string): Date | null {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function normalizeTimeZone(value: string | null | undefined): string {
  const candidate = value?.trim();
  if (!candidate) {
    return DEFAULT_DISPLAY_TIME_ZONE;
  }
  try {
    Intl.DateTimeFormat(undefined, { timeZone: candidate });
    return candidate;
  } catch {
    return DEFAULT_DISPLAY_TIME_ZONE;
  }
}

export function setDisplayTimeZone(value: string | null | undefined): void {
  displayTimeZone = normalizeTimeZone(value);
}

export function getDisplayTimeZone(): string {
  return displayTimeZone;
}

function formatTimePart(value: Date, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone,
  }).format(value).toLowerCase();
}

function dateOptions(timeZone: string): Intl.DateTimeFormatOptions {
  return {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone,
  };
}

export function formatDate(value: string): string {
  const parsed = parseDate(value);
  if (!parsed) return value;
  return new Intl.DateTimeFormat(undefined, dateOptions(displayTimeZone)).format(parsed);
}

export function formatDateTime(value: string): string {
  const parsed = parseDate(value);
  if (!parsed) return value;
  const datePart = new Intl.DateTimeFormat(undefined, dateOptions(displayTimeZone)).format(parsed);
  const timePart = formatTimePart(parsed, displayTimeZone);
  return `${datePart} ${timePart}`;
}
