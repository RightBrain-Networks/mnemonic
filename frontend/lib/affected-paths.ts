export const AFFECTED_PATHS_MAX_ITEMS = 64;
export const AFFECTED_PATH_MAX_BYTES = 512;
export const AFFECTED_PATHS_MAX_BYTES = 16_384;

const SAFE_COMPONENT = /^[A-Za-z0-9._@+=,~*-]+$/;

export interface AffectedPathsIssue {
  readonly message: string;
  readonly index?: number;
}

export class AffectedPathsValidationError extends Error {
  readonly index?: number;

  constructor(issue: AffectedPathsIssue) {
    super(issue.message);
    this.name = "AffectedPathsValidationError";
    this.index = issue.index;
  }
}

function indexedMessage(index: number, message: string): AffectedPathsIssue {
  return { index, message: `Affected path ${index + 1}: ${message}` };
}

function isAscii(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    if (value.charCodeAt(index) > 0x7f) return false;
  }
  return true;
}

function validComponent(component: string): boolean {
  if (
    !component
    || component === "."
    || component === ".."
    || !SAFE_COMPONENT.test(component)
  ) return false;
  return component === "**" || !component.includes("**");
}

export function affectedPathsIssue(value: unknown): AffectedPathsIssue | null {
  if (!Array.isArray(value)) {
    return { message: "Affected paths must be a list." };
  }
  if (value.length > AFFECTED_PATHS_MAX_ITEMS) {
    return { message: `Affected paths may contain at most ${AFFECTED_PATHS_MAX_ITEMS} entries.` };
  }

  for (const [index, entry] of value.entries()) {
    if (typeof entry !== "string") {
      return indexedMessage(index, "enter one text pattern per line.");
    }
    const bytes = new TextEncoder().encode(entry).byteLength;
    if (bytes === 0) return indexedMessage(index, "empty patterns are not allowed.");
    if (bytes > AFFECTED_PATH_MAX_BYTES) {
      return indexedMessage(
        index,
        `patterns may contain at most ${AFFECTED_PATH_MAX_BYTES} bytes.`
      );
    }
  }

  for (const [index, entry] of value.entries()) {
    if (
      typeof entry !== "string"
      || !isAscii(entry)
      || entry.startsWith("/")
      || entry.endsWith("/")
      || entry.split("/").some((component) => !validComponent(component))
    ) {
      return indexedMessage(
        index,
        "use only safe ASCII path components and *, with ** only as a complete component."
      );
    }
  }

  const seen = new Set<string>();
  for (const [index, entry] of value.entries()) {
    if (typeof entry !== "string") continue;
    if (seen.has(entry)) return indexedMessage(index, "duplicate patterns are not allowed.");
    seen.add(entry);
  }

  const aggregateBytes = value.reduce(
    (total, entry) => total + (typeof entry === "string"
      ? new TextEncoder().encode(entry).byteLength
      : 0),
    0
  );
  if (aggregateBytes > AFFECTED_PATHS_MAX_BYTES) {
    return {
      message: `Affected paths may contain at most ${AFFECTED_PATHS_MAX_BYTES} bytes in total.`
    };
  }
  return null;
}

export function validAffectedPaths(value: unknown): value is string[] {
  return affectedPathsIssue(value) === null;
}

export function parseAffectedPathsDraft(value: string): string[] {
  const paths = value === "" ? [] : value.split("\n");
  const issue = affectedPathsIssue(paths);
  if (issue) throw new AffectedPathsValidationError(issue);
  return paths;
}
