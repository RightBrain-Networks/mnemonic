import type {
  ArtifactReferenceInput,
  ArtifactReferenceRead,
  ArtifactType,
  CompletionEvidenceEpisodeRead,
  CompletionEvidenceInput,
  CompletionEvidencePage,
  CompletionEvidencePayloadRead,
  VerificationOutcome,
  VerificationResultInput,
  VerificationResultRead,
  VerificationType,
  WorkStatus
} from "./types.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  nullableBoundedText,
  nullableUuid,
  objectValue,
  sameUuid,
  validUnicode,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

export const COMPLETION_EVIDENCE_MAX_ENTRIES = 20;
export const COMPLETION_EVIDENCE_MAX_BYTES = 32_768;
export const COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES = 3_145_728;
export const COMPLETION_EVIDENCE_PAGE_LIMIT = 10;
export const MAX_COMPLETION_EVENT_ID = 9_223_372_036_854_775_806n;

const encoder = new TextEncoder();
const CANONICAL_SERVER_CREATED_AT_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$/;
const VERIFICATION_TYPES = new Set<VerificationType>(["command", "observation"]);
const OUTCOMES = new Set<VerificationOutcome>([
  "passed",
  "failed",
  "inconclusive",
  "skipped"
]);
const ARTIFACT_TYPES = new Set<ArtifactType>([
  "commit",
  "pull_request",
  "branch",
  "test_run",
  "repository_path",
  "external_issue",
  "build_artifact"
]);

function validCanonicalServerCreatedAt(value: unknown): value is string {
  return typeof value === "string"
    && CANONICAL_SERVER_CREATED_AT_PATTERN.test(value)
    && validUtcDateTime(value);
}
const EXTERNAL_ARTIFACT_TYPES = new Set<ArtifactType>([
  "pull_request",
  "test_run",
  "external_issue",
  "build_artifact"
]);
const WORK_STATUSES = new Set<WorkStatus>([
  "pending",
  "deferred",
  "done",
  "wont-do",
  "promoted"
]);
const CHECKPOINT_KINDS = new Set(["context", "progress", "completion"]);
const RESULT_KEYS = new Set([
  "verification_type",
  "name",
  "outcome",
  "summary",
  "command",
  "exit_code",
  "observed_at",
  "observed_at_commit"
]);
const ARTIFACT_KEYS = new Set(["artifact_type", "label", "reference"]);
const COMMIT_PATTERN = /^[0-9a-f]{7,64}$/;
const EVENT_ID_PATTERN = /^[1-9][0-9]{0,18}$/;
const CURSOR_PATTERN = /^[A-Za-z0-9_-]+$/;
const EXIT_CODE_PATTERN = /^(?:0|-?[1-9][0-9]*)$/;
const COMPLETION_EVIDENCE_CURSOR_MAX_BYTES = 2_048;
const REPOSITORY_COMPONENT_PATTERN = /^[A-Za-z0-9._@+=,~-]+$/;
const TIMESTAMP_PATTERN =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/;

export type CompletionEvidenceErrorClass =
  | "type"
  | "extra"
  | "missing"
  | "blank"
  | "nul"
  | "characters"
  | "bytes"
  | "grammar"
  | "timestamp"
  | "matrix"
  | "duplicate"
  | "count"
  | "aggregate_bytes";

export interface CompletionEvidenceIssue {
  readonly path: readonly (string | number)[];
  readonly errorClass: CompletionEvidenceErrorClass;
  readonly message: string;
}

export class CompletionEvidenceValidationError extends Error {
  readonly issue: CompletionEvidenceIssue;

  constructor(issue: CompletionEvidenceIssue) {
    super(issue.message);
    this.name = "CompletionEvidenceValidationError";
    this.issue = issue;
  }
}

function issue(
  path: readonly (string | number)[],
  errorClass: CompletionEvidenceErrorClass,
  message: string
): CompletionEvidenceIssue {
  return { path, errorClass, message };
}

function utf8Bytes(value: string): number {
  return encoder.encode(value).byteLength;
}

function unicodeScalars(value: string): number {
  return Array.from(value).length;
}

// CPython str.isspace()/str.strip() intentionally differs from JavaScript
// trim(): it includes U+001C–U+001F and excludes U+FEFF. Evidence validation
// uses this frozen set across the API, MCP, PostgreSQL, and browser clients.
function isPythonWhitespace(codePoint: number): boolean {
  return (codePoint >= 0x0009 && codePoint <= 0x000d)
    || (codePoint >= 0x001c && codePoint <= 0x0020)
    || codePoint === 0x0085
    || codePoint === 0x00a0
    || codePoint === 0x1680
    || (codePoint >= 0x2000 && codePoint <= 0x200a)
    || codePoint === 0x2028
    || codePoint === 0x2029
    || codePoint === 0x202f
    || codePoint === 0x205f
    || codePoint === 0x3000;
}

function hasPythonNonWhitespace(value: string): boolean {
  return Array.from(value).some((character) => !isPythonWhitespace(character.codePointAt(0)!));
}

function hasPythonWhitespaceEdge(value: string): boolean {
  const characters = Array.from(value);
  return characters.length > 0 && (
    isPythonWhitespace(characters[0]!.codePointAt(0)!)
    || isPythonWhitespace(characters[characters.length - 1]!.codePointAt(0)!)
  );
}

export function completionEvidenceTextSize(value: string): {
  characters: number;
  bytes: number;
} {
  return { characters: unicodeScalars(value), bytes: utf8Bytes(value) };
}

function textIssues(
  value: unknown,
  path: readonly (string | number)[],
  label: string,
  maximumCharacters: number,
  maximumBytes: number | null
): CompletionEvidenceIssue[] {
  if (typeof value !== "string" || !validUnicode(value)) {
    return [issue(path, "type", `${label} must be valid Unicode text.`)];
  }
  if (value.includes("\0")) {
    return [issue(path, "nul", `${label} cannot contain NUL.`)];
  }
  if (!hasPythonNonWhitespace(value)) {
    return [issue(path, "blank", `${label} cannot be blank.`)];
  }
  if (unicodeScalars(value) > maximumCharacters) {
    return [issue(path, "characters", `${label} is too long.`)];
  }
  if (maximumBytes !== null && utf8Bytes(value) > maximumBytes) {
    return [issue(path, "bytes", `${label} uses too many UTF-8 bytes.`)];
  }
  return [];
}

function leapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function daysInMonth(year: number, month: number): number {
  const values = [31, leapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return values[month - 1] ?? 0;
}

function daysBeforeYear(year: number): number {
  const prior = year - 1;
  return 365 * prior + Math.floor(prior / 4) - Math.floor(prior / 100)
    + Math.floor(prior / 400);
}

function daysBeforeMonth(year: number, month: number): number {
  let total = 0;
  for (let current = 1; current < month; current += 1) {
    total += daysInMonth(year, current);
  }
  return total;
}

/**
 * Parses the deliberately narrow evidence timestamp without Date.parse/Date.UTC.
 * The returned spelling identifies the same instant in canonical UTC.
 */
export function canonicalObservedAt(value: unknown): string | null {
  if (
    typeof value !== "string"
    || !validUnicode(value)
    || value.length < 20
    || value.length > 32
    || utf8Bytes(value) !== value.length
  ) return null;
  const match = TIMESTAMP_PATTERN.exec(value);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const fraction = match[7] ?? "";
  const zone = match[8]!;
  if (
    year < 1
    || year > 9999
    || month < 1
    || month > 12
    || day < 1
    || day > daysInMonth(year, month)
    || hour > 23
    || minute > 59
    || second > 59
    || zone === "-00:00"
  ) return null;

  let offsetSeconds = 0;
  if (zone !== "Z") {
    const zoneHours = Number(zone.slice(1, 3));
    const zoneMinutes = Number(zone.slice(4, 6));
    if (zoneHours > 14 || zoneMinutes > 59 || (zoneHours === 14 && zoneMinutes !== 0)) {
      return null;
    }
    offsetSeconds = (zoneHours * 60 + zoneMinutes) * 60 * (zone[0] === "+" ? 1 : -1);
  }

  const localDay = daysBeforeYear(year) + daysBeforeMonth(year, month) + day - 1;
  const localSecond = BigInt(localDay * 86_400 + hour * 3_600 + minute * 60 + second);
  const micros = localSecond * 1_000_000n
    + BigInt(fraction.padEnd(6, "0") || "0")
    - BigInt(offsetSeconds) * 1_000_000n;
  const maximum = BigInt(daysBeforeYear(10_000)) * 86_400_000_000n;
  if (micros < 0n || micros >= maximum) return null;

  const utcSecond = micros / 1_000_000n;
  const microsecond = Number(micros % 1_000_000n);
  const utcDay = Number(utcSecond / 86_400n);
  const secondOfDay = Number(utcSecond % 86_400n);
  let low = 1;
  let high = 10_000;
  while (low + 1 < high) {
    const midpoint = Math.floor((low + high) / 2);
    if (daysBeforeYear(midpoint) <= utcDay) low = midpoint;
    else high = midpoint;
  }
  const utcYear = low;
  let dayOfYear = utcDay - daysBeforeYear(utcYear);
  let utcMonth = 1;
  while (dayOfYear >= daysInMonth(utcYear, utcMonth)) {
    dayOfYear -= daysInMonth(utcYear, utcMonth);
    utcMonth += 1;
  }
  const utcDayOfMonth = dayOfYear + 1;
  const utcHour = Math.floor(secondOfDay / 3_600);
  const utcMinute = Math.floor((secondOfDay % 3_600) / 60);
  const utcSecondOfMinute = secondOfDay % 60;
  const date = `${String(utcYear).padStart(4, "0")}-${String(utcMonth).padStart(2, "0")}-${String(utcDayOfMonth).padStart(2, "0")}`;
  const time = `${String(utcHour).padStart(2, "0")}:${String(utcMinute).padStart(2, "0")}:${String(utcSecondOfMinute).padStart(2, "0")}`;
  return `${date}T${time}${microsecond ? `.${String(microsecond).padStart(6, "0")}` : ""}Z`;
}

export function isExternalArtifactType(value: ArtifactType): boolean {
  return EXTERNAL_ARTIFACT_TYPES.has(value);
}

export function validExternalArtifactUrl(value: unknown): value is string {
  if (
    typeof value !== "string"
    || value.length === 0
    || value.length > 2_000
    || utf8Bytes(value) !== value.length
    || !value.startsWith("https://")
    || Array.from(value).some((character) => {
      const code = character.charCodeAt(0);
      return code <= 0x20 || code >= 0x7f;
    })
  ) return false;
  try {
    const parsed = new URL(value);
    if (
      parsed.protocol !== "https:"
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash
      || !parsed.hostname
      || !/^\/(?:[A-Za-z0-9._~!$&'()*+,;=:@-]|\/|%[0-9A-F]{2})*$/.test(parsed.pathname)
      || parsed.toString() !== value
    ) return false;
    const hostname = parsed.hostname;
    if (hostname.startsWith("[") && hostname.endsWith("]")) {
      return /^[\[\]0-9a-f:.]+$/.test(hostname);
    }
    if (hostname.length > 253) return false;
    return hostname.split(".").every((label) => (
      label.length > 0
      && label.length <= 63
      && /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
    ));
  } catch {
    return false;
  }
}

export function artifactNavigationHref(artifact: ArtifactReferenceInput): string | null {
  return isExternalArtifactType(artifact.artifact_type)
    && validExternalArtifactUrl(artifact.reference)
    ? artifact.reference
    : null;
}

function validRepositoryPath(value: unknown): value is string {
  return typeof value === "string"
    && value.length > 0
    && value.length <= 512
    && utf8Bytes(value) === value.length
    && value.split("/").every((component) => (
      component !== "."
      && component !== ".."
      && REPOSITORY_COMPONENT_PATTERN.test(component)
    ));
}

function artifactReferenceValid(type: ArtifactType, value: unknown): boolean {
  if (type === "commit") return typeof value === "string" && COMMIT_PATTERN.test(value);
  if (type === "branch") {
    return typeof value === "string"
      && validUnicode(value)
      && !value.includes("\0")
      && hasPythonNonWhitespace(value)
      && !hasPythonWhitespaceEdge(value)
      && unicodeScalars(value) <= 200
      && utf8Bytes(value) <= 800;
  }
  if (type === "repository_path") return validRepositoryPath(value);
  return validExternalArtifactUrl(value);
}

export function completionEvidenceAggregateBytes(value: CompletionEvidenceInput): number {
  let total = 0;
  for (const result of value.verification_results ?? []) {
    total += utf8Bytes(result.verification_type);
    total += utf8Bytes(result.name);
    total += utf8Bytes(result.outcome);
    total += utf8Bytes(result.summary);
    if ("command" in result && result.command !== undefined) total += utf8Bytes(result.command);
    if (result.observed_at !== undefined) total += 32;
    if (result.observed_at_commit !== undefined) total += utf8Bytes(result.observed_at_commit);
  }
  for (const artifact of value.artifact_references ?? []) {
    total += utf8Bytes(artifact.artifact_type);
    total += utf8Bytes(artifact.label);
    total += utf8Bytes(artifact.reference);
  }
  return total;
}

export function completionEvidenceIssues(value: unknown): CompletionEvidenceIssue[] {
  const base = ["completion_evidence"] as const;
  if (value === undefined) return [];
  const evidence = objectValue(value);
  if (!evidence) {
    return [issue(base, "type", "Completion evidence must be an object.")];
  }
  const issues: CompletionEvidenceIssue[] = [];
  for (const key of Object.keys(evidence)) {
    if (key !== "verification_results" && key !== "artifact_references") {
      issues.push(issue([...base, key], "extra", "Completion evidence contains an unsupported field."));
    }
  }
  const resultsValue = evidence.verification_results;
  const artifactsValue = evidence.artifact_references;
  if (resultsValue !== undefined && !Array.isArray(resultsValue)) {
    issues.push(issue([...base, "verification_results"], "type", "Verification results must be an array."));
  }
  if (artifactsValue !== undefined && !Array.isArray(artifactsValue)) {
    issues.push(issue([...base, "artifact_references"], "type", "Artifact references must be an array."));
  }
  const results = Array.isArray(resultsValue) ? resultsValue : [];
  const artifacts = Array.isArray(artifactsValue) ? artifactsValue : [];
  if (results.length + artifacts.length > COMPLETION_EVIDENCE_MAX_ENTRIES) {
    issues.push(issue(base, "count", "Completion evidence can contain at most 20 total entries."));
  }

  results.forEach((entry, index) => {
    const path = [...base, "verification_results", index] as const;
    const result = objectValue(entry);
    if (!result) {
      issues.push(issue(path, "type", "A verification result must be an object."));
      return;
    }
    for (const key of Object.keys(result)) {
      if (!RESULT_KEYS.has(key)) {
        issues.push(issue([...path, key], "extra", "Verification result contains an unsupported field."));
      }
    }
    if (typeof result.verification_type !== "string" || !VERIFICATION_TYPES.has(result.verification_type as VerificationType)) {
      issues.push(issue([...path, "verification_type"], "type", "Choose command or observation."));
    }
    if (typeof result.outcome !== "string" || !OUTCOMES.has(result.outcome as VerificationOutcome)) {
      issues.push(issue([...path, "outcome"], "type", "Choose a supported reported outcome."));
    }
    issues.push(...textIssues(result.name, [...path, "name"], "Result name", 200, 800));
    issues.push(...textIssues(result.summary, [...path, "summary"], "Result summary", 4_000, 16_000));
    if (result.observed_at !== undefined && canonicalObservedAt(result.observed_at) === null) {
      issues.push(issue([...path, "observed_at"], "timestamp", "Observed at must use the supported RFC 3339 spelling."));
    }
    if (
      result.observed_at_commit !== undefined
      && (typeof result.observed_at_commit !== "string"
        || !COMMIT_PATTERN.test(result.observed_at_commit))
    ) {
      issues.push(issue([...path, "observed_at_commit"], "grammar", "Observed commit must be 7–64 lowercase hexadecimal characters."));
    }

    if (result.verification_type === "command") {
      issues.push(...textIssues(result.command, [...path, "command"], "Command", 4_096, 16_384));
      if (result.outcome === "skipped") {
        issues.push(issue([...path, "outcome"], "matrix", "A command result cannot be reported as skipped."));
      }
      const hasExit = Object.hasOwn(result, "exit_code");
      if (hasExit && !finiteInteger(result.exit_code, -2_147_483_648, 2_147_483_647)) {
        issues.push(issue([...path, "exit_code"], "type", "Exit code must be a signed 32-bit integer."));
      } else if (result.outcome === "passed" && (!hasExit || result.exit_code !== 0)) {
        issues.push(issue([...path, "exit_code"], hasExit ? "matrix" : "missing", "A passed command requires exit code 0."));
      } else if (result.outcome === "failed" && !hasExit) {
        issues.push(issue([...path, "exit_code"], "missing", "A failed command requires a nonzero exit code."));
      } else if (result.outcome === "failed" && result.exit_code === 0) {
        issues.push(issue([...path, "exit_code"], "matrix", "A failed command requires a nonzero exit code."));
      } else if (result.outcome === "inconclusive" && hasExit) {
        issues.push(issue([...path, "exit_code"], "extra", "An inconclusive command cannot include an exit code."));
      }
    } else if (result.verification_type === "observation") {
      if (Object.hasOwn(result, "command")) {
        issues.push(issue([...path, "command"], "extra", "An observation cannot include a command."));
      }
      if (Object.hasOwn(result, "exit_code")) {
        issues.push(issue([...path, "exit_code"], "extra", "An observation cannot include an exit code."));
      }
    }
  });

  const seenArtifacts = new Set<string>();
  artifacts.forEach((entry, index) => {
    const path = [...base, "artifact_references", index] as const;
    const artifact = objectValue(entry);
    if (!artifact) {
      issues.push(issue(path, "type", "An artifact reference must be an object."));
      return;
    }
    for (const key of Object.keys(artifact)) {
      if (!ARTIFACT_KEYS.has(key)) {
        issues.push(issue([...path, key], "extra", "Artifact reference contains an unsupported field."));
      }
    }
    const artifactType = typeof artifact.artifact_type === "string"
      && ARTIFACT_TYPES.has(artifact.artifact_type as ArtifactType)
      ? artifact.artifact_type as ArtifactType
      : null;
    if (!artifactType) {
      issues.push(issue([...path, "artifact_type"], "type", "Choose a supported artifact type."));
    }
    issues.push(...textIssues(artifact.label, [...path, "label"], "Artifact label", 200, 800));
    if (!artifactType || !artifactReferenceValid(artifactType, artifact.reference)) {
      issues.push(issue([...path, "reference"], "grammar", "Artifact reference does not match its selected type."));
    } else {
      const identity = `${artifactType}\0${artifact.reference as string}`;
      if (seenArtifacts.has(identity)) {
        issues.push(issue([...path, "reference"], "duplicate", "This exact artifact reference is already included."));
      }
      seenArtifacts.add(identity);
    }
  });

  if (issues.length === 0) {
    const typed = {
      verification_results: results,
      artifact_references: artifacts
    } as unknown as CompletionEvidenceInput;
    if (completionEvidenceAggregateBytes(typed) > COMPLETION_EVIDENCE_MAX_BYTES) {
      issues.push(issue(base, "aggregate_bytes", "Completion evidence exceeds 32,768 UTF-8 bytes."));
    }
  }
  return issues;
}

export function normalizeCompletionEvidenceInput(
  value: unknown
): CompletionEvidenceInput | null {
  if (value === undefined) return null;
  const issues = completionEvidenceIssues(value);
  if (issues.length) throw new CompletionEvidenceValidationError(issues[0]!);
  const evidence = objectValue(value)!;
  const results = (evidence.verification_results as Record<string, unknown>[] | undefined ?? [])
    .map((result) => {
      const normalized: Record<string, unknown> = {
        verification_type: result.verification_type,
        name: result.name,
        outcome: result.outcome,
        summary: result.summary
      };
      if (result.verification_type === "command") normalized.command = result.command;
      if (Object.hasOwn(result, "exit_code")) normalized.exit_code = result.exit_code;
      if (result.observed_at !== undefined) {
        normalized.observed_at = canonicalObservedAt(result.observed_at);
      }
      if (result.observed_at_commit !== undefined) {
        normalized.observed_at_commit = result.observed_at_commit;
      }
      return normalized as unknown as VerificationResultInput;
    });
  const artifacts = (evidence.artifact_references as ArtifactReferenceInput[] | undefined ?? [])
    .map((artifact) => ({
      artifact_type: artifact.artifact_type,
      label: artifact.label,
      reference: artifact.reference
    }));
  if (results.length + artifacts.length === 0) return null;
  return {
    verification_results: results,
    artifact_references: artifacts
  };
}

export interface VerificationResultDraft {
  readonly key: string;
  verificationType: VerificationType;
  name: string;
  outcome: VerificationOutcome;
  summary: string;
  command: string;
  exitCode: string;
  observedAt: string;
  observedAtCommit: string;
}

export interface ArtifactReferenceDraft {
  readonly key: string;
  artifactType: ArtifactType;
  label: string;
  reference: string;
}

export interface CompletionEvidenceDraft {
  verificationResults: VerificationResultDraft[];
  artifactReferences: ArtifactReferenceDraft[];
}

export function emptyCompletionEvidenceDraft(): CompletionEvidenceDraft {
  return { verificationResults: [], artifactReferences: [] };
}

export function completionEvidenceDraftIsEmpty(draft: CompletionEvidenceDraft): boolean {
  return draft.verificationResults.length === 0 && draft.artifactReferences.length === 0;
}

export function completionEvidenceDraftAggregateBytes(draft: CompletionEvidenceDraft): number {
  let total = 0;
  for (const result of draft.verificationResults) {
    total += utf8Bytes(result.verificationType) + utf8Bytes(result.name)
      + utf8Bytes(result.outcome) + utf8Bytes(result.summary);
    if (result.verificationType === "command") total += utf8Bytes(result.command);
    if (result.observedAt) total += 32;
    if (result.observedAtCommit) total += utf8Bytes(result.observedAtCommit);
  }
  for (const artifact of draft.artifactReferences) {
    total += utf8Bytes(artifact.artifactType) + utf8Bytes(artifact.label)
      + utf8Bytes(artifact.reference);
  }
  return total;
}

export function completionEvidenceFromDraft(
  draft: CompletionEvidenceDraft
): CompletionEvidenceInput | null {
  const results: Record<string, unknown>[] = draft.verificationResults.map((result, index) => {
    const value: Record<string, unknown> = {
      verification_type: result.verificationType,
      name: result.name,
      outcome: result.outcome,
      summary: result.summary
    };
    if (result.verificationType === "command") {
      value.command = result.command;
      if (result.exitCode !== "") {
        if (!EXIT_CODE_PATTERN.test(result.exitCode)) {
          throw new CompletionEvidenceValidationError(issue(
            ["completion_evidence", "verification_results", index, "exit_code"],
            "type",
            result.exitCode === "-0"
              ? "Use 0 instead of -0; negative zero cannot be preserved on the wire."
              : "Exit code must be a signed 32-bit integer."
          ));
        }
        value.exit_code = Number(result.exitCode);
      }
    }
    if (result.observedAt !== "") value.observed_at = result.observedAt;
    if (result.observedAtCommit !== "") {
      value.observed_at_commit = result.observedAtCommit;
    }
    return value;
  });
  const artifacts = draft.artifactReferences.map((artifact) => ({
    artifact_type: artifact.artifactType,
    label: artifact.label,
    reference: artifact.reference
  }));
  return normalizeCompletionEvidenceInput({
    verification_results: results,
    artifact_references: artifacts
  });
}

export function completionEvidenceDraftIssues(
  draft: CompletionEvidenceDraft
): CompletionEvidenceIssue[] {
  const parseIssues: CompletionEvidenceIssue[] = [];
  const results: Record<string, unknown>[] = draft.verificationResults.map((result, index) => {
    const value: Record<string, unknown> = {
      verification_type: result.verificationType,
      name: result.name,
      outcome: result.outcome,
      summary: result.summary
    };
    if (result.verificationType === "command") {
      value.command = result.command;
      if (result.exitCode !== "") {
        if (EXIT_CODE_PATTERN.test(result.exitCode)) {
          value.exit_code = Number(result.exitCode);
        } else {
          parseIssues.push(issue(
            ["completion_evidence", "verification_results", index, "exit_code"],
            "type",
            result.exitCode === "-0"
              ? "Use 0 instead of -0; negative zero cannot be preserved on the wire."
              : "Exit code must be a signed 32-bit integer."
          ));
        }
      }
    }
    if (result.observedAt !== "") value.observed_at = result.observedAt;
    if (result.observedAtCommit !== "") value.observed_at_commit = result.observedAtCommit;
    return value;
  });
  return [
    ...parseIssues,
    ...completionEvidenceIssues({
      verification_results: results,
      artifact_references: draft.artifactReferences.map((artifact) => ({
        artifact_type: artifact.artifactType,
        label: artifact.label,
        reference: artifact.reference
      }))
    })
  ];
}

export function completionEvidenceIssueField(
  issueValue: CompletionEvidenceIssue,
  family: "verification_results" | "artifact_references",
  index: number,
  field?: string
): boolean {
  const path = issueValue.path;
  return path[1] === family
    && path[2] === index
    && (field === undefined || path[3] === field);
}

function validEventId(value: unknown): value is string {
  if (typeof value !== "string" || !EVENT_ID_PATTERN.test(value)) return false;
  try {
    return BigInt(value) <= MAX_COMPLETION_EVENT_ID;
  } catch {
    return false;
  }
}

const CHECKPOINT_POINTER_READ_KEYS = [
  "id", "work_item_id", "kind", "source_client", "source_session_id",
  "source_model", "repository_branch", "verified_against", "tags",
  "migration_origin", "legacy_record_id", "created_at"
] as const;
const RESULT_READ_REQUIRED_KEYS = [
  "id", "work_item_id", "completion_checkpoint_id", "position",
  "verification_type", "name", "outcome", "summary", "created_at"
] as const;
const RESULT_READ_OPTIONAL_KEYS = [
  "command", "exit_code", "observed_at", "observed_at_commit"
] as const;
const COMMAND_RESULT_READ_KEYS = [
  ...RESULT_READ_REQUIRED_KEYS,
  ...RESULT_READ_OPTIONAL_KEYS
] as const;
const OBSERVATION_RESULT_READ_KEYS = [
  ...RESULT_READ_REQUIRED_KEYS,
  "observed_at", "observed_at_commit"
] as const;
const ARTIFACT_READ_KEYS = [
  "id", "work_item_id", "completion_checkpoint_id", "position",
  "artifact_type", "label", "reference", "created_at"
] as const;
const EVIDENCE_PAYLOAD_READ_KEYS = [
  "verification_results", "artifact_references"
] as const;
const EVIDENCE_EPISODE_READ_KEYS = [
  "completion_event_id", "completion_checkpoint",
  "verification_results", "artifact_references"
] as const;
const EVIDENCE_PAGE_READ_KEYS = [
  "work_item_id", "work_version", "lifecycle_status", "is_duplicate",
  "canonical_work_item_id", "current_completion_checkpoint_id",
  "as_of_completion_event_id", "items", "total",
  "structured_completion_total", "limit", "next_cursor"
] as const;

export const COMPLETION_EVIDENCE_DECODER_FIELDS = {
  decodeCheckpointPointer: CHECKPOINT_POINTER_READ_KEYS,
  "decodeVerificationResult:command": COMMAND_RESULT_READ_KEYS,
  "decodeVerificationResult:observation": OBSERVATION_RESULT_READ_KEYS,
  decodeArtifactReference: ARTIFACT_READ_KEYS,
  decodeCompletionEvidencePayload: EVIDENCE_PAYLOAD_READ_KEYS,
  "decodeCompletionEvidencePage:item": EVIDENCE_EPISODE_READ_KEYS,
  decodeCompletionEvidencePage: EVIDENCE_PAGE_READ_KEYS
} as const;

function decodeCheckpointPointer(value: unknown, workItemId: string) {
  const checkpoint = objectValue(value);
  if (
    !checkpoint
    || !exactKeys(checkpoint, CHECKPOINT_POINTER_READ_KEYS)
    || !validUuid(checkpoint.id)
    || !sameUuid(checkpoint.work_item_id, workItemId)
    || typeof checkpoint.kind !== "string"
    || !CHECKPOINT_KINDS.has(checkpoint.kind)
    || checkpoint.kind !== "completion"
    || !boundedText(checkpoint.source_client, 80)
    || !boundedText(checkpoint.source_session_id, 200)
    || !nullableBoundedText(checkpoint.source_model, 120)
    || !nullableBoundedText(checkpoint.repository_branch, 200)
    || !(checkpoint.verified_against === null
      || typeof checkpoint.verified_against === "string" && /^[a-fA-F0-9]{7,64}$/.test(checkpoint.verified_against))
    || !Array.isArray(checkpoint.tags)
    || checkpoint.tags.some((tag) => !boundedText(tag, 50) || tag !== tag.toLowerCase())
    || new Set(checkpoint.tags).size !== checkpoint.tags.length
    || !(checkpoint.migration_origin === null
      || checkpoint.migration_origin === "legacy-handoff-snapshot"
      || checkpoint.migration_origin === "legacy-comment")
    || !nullableUuid(checkpoint.legacy_record_id)
    || !validCanonicalServerCreatedAt(checkpoint.created_at)
  ) throw new Error("Mnemonic returned invalid completion evidence.");
  return checkpoint as unknown as CompletionEvidenceEpisodeRead["completion_checkpoint"];
}

function decodeVerificationResult(
  value: unknown,
  workItemId: string,
  checkpointId: string,
  checkpointCreatedAt: string,
  position: number
): VerificationResultRead {
  const result = objectValue(value);
  if (
    !result
    || !RESULT_READ_REQUIRED_KEYS.every((key) => Object.hasOwn(result, key))
    || Object.keys(result).some((key) => (
      !RESULT_READ_REQUIRED_KEYS.includes(key as typeof RESULT_READ_REQUIRED_KEYS[number])
      && !RESULT_READ_OPTIONAL_KEYS.includes(key as typeof RESULT_READ_OPTIONAL_KEYS[number])
    ))
    || !validUuid(result.id)
    || !sameUuid(result.work_item_id, workItemId)
    || !sameUuid(result.completion_checkpoint_id, checkpointId)
    || result.position !== position
    || !validCanonicalServerCreatedAt(result.created_at)
    || result.created_at !== checkpointCreatedAt
  ) throw new Error("Mnemonic returned invalid completion evidence.");
  const content: Record<string, unknown> = {};
  for (const key of RESULT_KEYS) {
    if (Object.hasOwn(result, key)) content[key] = result[key];
  }
  const normalized = normalizeCompletionEvidenceInput({ verification_results: [content] });
  if (!normalized || normalized.verification_results?.length !== 1) {
    throw new Error("Mnemonic returned invalid completion evidence.");
  }
  const canonical = normalized.verification_results[0]!;
  for (const key of Object.keys(content)) {
    if (content[key] !== (canonical as unknown as Record<string, unknown>)[key]) {
      throw new Error("Mnemonic returned noncanonical completion evidence.");
    }
  }
  return { ...result, ...canonical } as unknown as VerificationResultRead;
}

function decodeArtifactReference(
  value: unknown,
  workItemId: string,
  checkpointId: string,
  checkpointCreatedAt: string,
  position: number
): ArtifactReferenceRead {
  const artifact = objectValue(value);
  if (
    !artifact
    || !exactKeys(artifact, ARTIFACT_READ_KEYS)
    || !validUuid(artifact.id)
    || !sameUuid(artifact.work_item_id, workItemId)
    || !sameUuid(artifact.completion_checkpoint_id, checkpointId)
    || artifact.position !== position
    || !validCanonicalServerCreatedAt(artifact.created_at)
    || artifact.created_at !== checkpointCreatedAt
  ) throw new Error("Mnemonic returned invalid completion evidence.");
  const normalized = normalizeCompletionEvidenceInput({
    artifact_references: [{
      artifact_type: artifact.artifact_type,
      label: artifact.label,
      reference: artifact.reference
    }]
  });
  if (!normalized || normalized.artifact_references?.length !== 1) {
    throw new Error("Mnemonic returned invalid completion evidence.");
  }
  return {
    ...artifact,
    ...normalized.artifact_references[0]
  } as unknown as ArtifactReferenceRead;
}

export function decodeCompletionEvidencePayload(
  value: unknown,
  workItemId: string,
  checkpointId: string,
  checkpointCreatedAt: string,
  allowEmpty = false
): CompletionEvidencePayloadRead {
  const payload = objectValue(value);
  if (
    !payload
    || !exactKeys(payload, EVIDENCE_PAYLOAD_READ_KEYS)
    || !Array.isArray(payload.verification_results)
    || !Array.isArray(payload.artifact_references)
    || payload.verification_results.length + payload.artifact_references.length
      > COMPLETION_EVIDENCE_MAX_ENTRIES
    || (!allowEmpty
      && payload.verification_results.length + payload.artifact_references.length === 0)
  ) throw new Error("Mnemonic returned invalid completion evidence.");
  const results = payload.verification_results.map((entry, index) => (
    decodeVerificationResult(entry, workItemId, checkpointId, checkpointCreatedAt, index)
  ));
  const artifacts = payload.artifact_references.map((entry, index) => (
    decodeArtifactReference(entry, workItemId, checkpointId, checkpointCreatedAt, index)
  ));
  if (
    new Set(results.map((entry) => entry.id.toLowerCase())).size !== results.length
    || new Set(artifacts.map((entry) => entry.id.toLowerCase())).size !== artifacts.length
  ) throw new Error("Mnemonic returned invalid completion evidence.");
  const input = {
    verification_results: results.map((entry) => {
      const content: Record<string, unknown> = {};
      for (const key of RESULT_KEYS) {
        if (Object.hasOwn(entry, key)) content[key] = entry[key as keyof VerificationResultRead];
      }
      return content;
    }),
    artifact_references: artifacts.map(({ artifact_type, label, reference }) => ({
      artifact_type,
      label,
      reference
    }))
  };
  const issues = completionEvidenceIssues(input);
  if (issues.length) throw new Error("Mnemonic returned invalid completion evidence.");
  return { verification_results: results, artifact_references: artifacts };
}

function canonicalEvidenceContent(payload: CompletionEvidencePayloadRead): CompletionEvidenceInput {
  return {
    verification_results: payload.verification_results.map((entry) => {
      const result: Record<string, unknown> = {};
      for (const key of RESULT_KEYS) {
        if (Object.hasOwn(entry, key)) result[key] = entry[key as keyof VerificationResultRead];
      }
      return result as unknown as VerificationResultInput;
    }),
    artifact_references: payload.artifact_references.map((entry) => ({
      artifact_type: entry.artifact_type,
      label: entry.label,
      reference: entry.reference
    }))
  };
}

export function completionEvidencePayloadMatchesInput(
  payload: CompletionEvidencePayloadRead,
  input: unknown
): boolean {
  try {
    const expected = normalizeCompletionEvidenceInput(input);
    return expected !== null
      && JSON.stringify(canonicalEvidenceContent(payload)) === JSON.stringify(expected);
  } catch {
    return false;
  }
}

export function decodeCompletionEvidencePage(
  value: unknown,
  expectedWorkItemId: string,
  isHead = true
): CompletionEvidencePage {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, EVIDENCE_PAGE_READ_KEYS)
    || !sameUuid(page.work_item_id, expectedWorkItemId)
    || !finiteInteger(page.work_version, 1)
    || typeof page.lifecycle_status !== "string"
    || !WORK_STATUSES.has(page.lifecycle_status as WorkStatus)
    || typeof page.is_duplicate !== "boolean"
    || !validUuid(page.canonical_work_item_id)
    || !nullableUuid(page.current_completion_checkpoint_id)
    || !(page.as_of_completion_event_id === null || validEventId(page.as_of_completion_event_id))
    || !Array.isArray(page.items)
    || !finiteInteger(page.total, 0)
    || !finiteInteger(page.structured_completion_total, 0, page.total as number)
    || !finiteInteger(page.limit, 1, COMPLETION_EVIDENCE_PAGE_LIMIT)
    || page.items.length > (page.limit as number)
    || !(page.next_cursor === null
      || typeof page.next_cursor === "string"
        && page.next_cursor.length >= 1
        && page.next_cursor.length <= 4_096
        && CURSOR_PATTERN.test(page.next_cursor)
        && page.next_cursor.length % 4 !== 1
        && Math.floor(page.next_cursor.length * 3 / 4)
          <= COMPLETION_EVIDENCE_CURSOR_MAX_BYTES)
    || (page.next_cursor !== null
      && (page.items.length !== page.limit || page.items.length >= page.total))
    || (isHead && page.next_cursor === null && page.items.length !== page.total)
    || ((page.is_duplicate || page.lifecycle_status !== "done")
      && page.current_completion_checkpoint_id !== null)
    || (!page.is_duplicate && !sameUuid(page.canonical_work_item_id, expectedWorkItemId))
    || (page.is_duplicate && sameUuid(page.canonical_work_item_id, expectedWorkItemId))
    || (page.total === 0
      && (page.as_of_completion_event_id !== null || page.items.length !== 0 || page.next_cursor !== null))
    || (page.total !== 0 && page.as_of_completion_event_id === null)
  ) throw new Error("Mnemonic returned an invalid completion-evidence page.");

  const highWater = page.as_of_completion_event_id === null
    ? null
    : BigInt(page.as_of_completion_event_id as string);
  let priorEventId: bigint | null = null;
  const items = page.items.map((entry) => {
    const episode = objectValue(entry);
    if (
      !episode
      || !exactKeys(episode, EVIDENCE_EPISODE_READ_KEYS)
      || !validEventId(episode.completion_event_id)
      || highWater === null
      || BigInt(episode.completion_event_id) > highWater
      || priorEventId !== null && BigInt(episode.completion_event_id) >= priorEventId
    ) throw new Error("Mnemonic returned an invalid completion-evidence page.");
    priorEventId = BigInt(episode.completion_event_id);
    const checkpoint = decodeCheckpointPointer(episode.completion_checkpoint, expectedWorkItemId);
    const payload = decodeCompletionEvidencePayload(
      {
        verification_results: episode.verification_results,
        artifact_references: episode.artifact_references
      },
      expectedWorkItemId,
      checkpoint.id,
      checkpoint.created_at,
      true
    );
    return {
      completion_event_id: episode.completion_event_id,
      completion_checkpoint: checkpoint,
      ...payload
    };
  });
  if (items.length > page.total) {
    throw new Error("Mnemonic returned an invalid completion-evidence page.");
  }
  const visibleStructured = items.filter((item) => (
    item.verification_results.length > 0 || item.artifact_references.length > 0
  )).length;
  const checkpointIds = items.map((item) => item.completion_checkpoint.id.toLowerCase());
  const resultIds = items.flatMap((item) => (
    item.verification_results.map((result) => result.id.toLowerCase())
  ));
  const artifactIds = items.flatMap((item) => (
    item.artifact_references.map((artifact) => artifact.id.toLowerCase())
  ));
  if (
    visibleStructured > page.structured_completion_total
    || new Set(checkpointIds).size !== checkpointIds.length
    || new Set(resultIds).size !== resultIds.length
    || new Set(artifactIds).size !== artifactIds.length
    || (isHead
      && items.length > 0
      && items[0]!.completion_event_id !== page.as_of_completion_event_id)
    || (isHead
      && page.next_cursor === null
      && visibleStructured !== page.structured_completion_total)
  ) {
    throw new Error("Mnemonic returned an invalid completion-evidence page.");
  }
  if (
    isHead
    && page.current_completion_checkpoint_id !== null
    && (!items[0]
      || !sameUuid(
        page.current_completion_checkpoint_id,
        items[0].completion_checkpoint.id
      ))
  ) throw new Error("Mnemonic returned an invalid completion-evidence page.");
  return { ...page, items } as unknown as CompletionEvidencePage;
}

export function mergeCompletionEvidencePage(
  head: CompletionEvidencePage,
  loaded: readonly CompletionEvidenceEpisodeRead[],
  next: CompletionEvidencePage
): CompletionEvidenceEpisodeRead[] {
  if (
    next.as_of_completion_event_id !== head.as_of_completion_event_id
    || next.total !== head.total
    || next.structured_completion_total !== head.structured_completion_total
  ) throw new Error("Mnemonic returned an unstable completion-evidence page.");
  const sameCurrentCheckpoint = head.current_completion_checkpoint_id === null
    ? next.current_completion_checkpoint_id === null
    : next.current_completion_checkpoint_id !== null
      && sameUuid(
        next.current_completion_checkpoint_id,
        head.current_completion_checkpoint_id
      );
  if (
    !sameUuid(next.work_item_id, head.work_item_id)
    || next.work_version !== head.work_version
    || next.lifecycle_status !== head.lifecycle_status
    || next.is_duplicate !== head.is_duplicate
    || !sameUuid(next.canonical_work_item_id, head.canonical_work_item_id)
    || !sameCurrentCheckpoint
  ) {
    throw new Error(
      "Completion evidence changed while older history was loading. "
      + "Reload current history before continuing."
    );
  }

  const combined = [...loaded, ...next.items];
  const eventIds = combined.map((episode) => episode.completion_event_id);
  const checkpointIds = combined.map((episode) => (
    episode.completion_checkpoint.id.toLowerCase()
  ));
  const resultIds = combined.flatMap((episode) => (
    episode.verification_results.map((result) => result.id.toLowerCase())
  ));
  const artifactIds = combined.flatMap((episode) => (
    episode.artifact_references.map((artifact) => artifact.id.toLowerCase())
  ));
  if (
    new Set(eventIds).size !== eventIds.length
    || new Set(checkpointIds).size !== checkpointIds.length
    || new Set(resultIds).size !== resultIds.length
    || new Set(artifactIds).size !== artifactIds.length
  ) throw new Error("Mnemonic returned duplicate completion-evidence identities.");

  for (let index = 1; index < combined.length; index += 1) {
    if (
      BigInt(combined[index]!.completion_event_id)
      >= BigInt(combined[index - 1]!.completion_event_id)
    ) throw new Error("Mnemonic returned out-of-order completion-evidence episodes.");
  }
  const structuredCount = combined.filter((episode) => (
    episode.verification_results.length > 0 || episode.artifact_references.length > 0
  )).length;
  if (
    combined.length > next.total
    || (next.next_cursor === null && combined.length !== next.total)
    || (next.next_cursor !== null && combined.length >= next.total)
    || structuredCount > next.structured_completion_total
    || (next.next_cursor === null && structuredCount !== next.structured_completion_total)
  ) throw new Error("Mnemonic returned inconsistent completion-evidence totals.");
  return combined;
}

export class IdentityEvidenceResponseError extends Error {
  constructor() {
    super("Mnemonic returned an invalid completion-evidence response.");
    this.name = "IdentityEvidenceResponseError";
  }
}

export function identityContentEncoding(headers: Headers): boolean {
  if (!headers.has("content-encoding")) return true;
  const value = headers.get("content-encoding");
  return value !== null && value.toLowerCase() === "identity";
}

async function cancelBodyWithoutReader(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch {
    // Cancellation is best-effort; it must not expose or consume the body.
  }
}

export async function readIdentityEvidenceBytes(
  response: Response,
  maximumBytes = COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES
): Promise<Uint8Array<ArrayBuffer>> {
  if (!identityContentEncoding(response.headers)) {
    await cancelBodyWithoutReader(response);
    throw new IdentityEvidenceResponseError();
  }
  const declared = response.headers.get("content-length");
  if (declared && /^[0-9]+$/.test(declared)) {
    try {
      if (BigInt(declared) > BigInt(maximumBytes)) {
        await cancelBodyWithoutReader(response);
        throw new IdentityEvidenceResponseError();
      }
    } catch (error) {
      if (error instanceof IdentityEvidenceResponseError) throw error;
    }
  }
  const reader = response.body?.getReader();
  if (!reader) throw new IdentityEvidenceResponseError();
  const bytes = new Uint8Array(maximumBytes);
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!value) continue;
      if (value.byteLength > maximumBytes - size) {
        await reader.cancel();
        throw new IdentityEvidenceResponseError();
      }
      bytes.set(value, size);
      size += value.byteLength;
    }
  } catch (error) {
    if (error instanceof IdentityEvidenceResponseError) throw error;
    try { await reader.cancel(); } catch { /* best effort */ }
    throw new IdentityEvidenceResponseError();
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
  return bytes.subarray(0, size);
}

export function decodeIdentityEvidenceJson(bytes: Uint8Array): unknown {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new IdentityEvidenceResponseError();
  }
}

export async function readIdentityEvidenceJson(response: Response): Promise<unknown> {
  return decodeIdentityEvidenceJson(await readIdentityEvidenceBytes(response));
}
