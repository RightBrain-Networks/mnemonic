import { api } from "./api.ts";
import { validReportPrompt } from "./job-completion-reports.ts";
import {
  boundedText, exactKeys, finiteInteger, objectValue, validUtcDateTime,
  validUuid, UUID_PATTERN
} from "./wire-guards.ts";

export const PROMPT_IDS = [
  "recall-pointer", "job-completion-report", "cold-code-review", "warm-code-review",
  "review-remediation", "resume-work", "review-recommendation"
] as const;
export type PromptId = typeof PROMPT_IDS[number];
export type PromptMetadata = {
  id: PromptId;
  name: string;
  description: string;
  size_bytes: number;
  created_at: string;
  updated_at: string;
  revision: string;
};
export type PromptDetail = PromptMetadata & { content: string };
export type PromptLibraryPage = {
  items: PromptMetadata[];
  macros: { macro: string; description: string }[];
};

const METADATA_KEYS = ["id", "name", "description", "size_bytes", "created_at", "updated_at", "revision"];
const UUID = UUID_PATTERN.source.slice(1, -1);
const LIST_ROUTE = new RegExp(`^projects/${UUID}/prompts$`);
const DETAIL_ROUTE = new RegExp(`^projects/${UUID}/prompts/(${PROMPT_IDS.join("|")})$`);
const RENDER_ROUTE = new RegExp(`^projects/${UUID}/prompts/(${PROMPT_IDS.join("|")})/render$`);
export const PROMPT_RESPONSE_MAX_BYTES = 4 * 1024 * 1024;

export function validPromptId(value: unknown): value is PromptId {
  return typeof value === "string" && PROMPT_IDS.includes(value as PromptId);
}

function validRevision(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

export function validPromptContent(id: PromptId, content: unknown): content is string {
  if (id === "job-completion-report") return validReportPrompt(content);
  if (id === "review-recommendation") return validReportPrompt(content)
    && Array.from(content).length <= 4_000 && new TextEncoder().encode(content).length <= 8_192;
  return boundedText(content, 100_000) && new TextEncoder().encode(content).length <= 400_000;
}

export function promptContentLimit(id: PromptId): string {
  if (id === "review-recommendation") return "Up to 4,000 characters and 8,192 UTF-8 bytes.";
  return id === "job-completion-report"
    ? "Up to 8,000 characters and 16,384 UTF-8 bytes."
    : "Up to 100,000 characters and 400,000 UTF-8 bytes.";
}

function invalidResponse(): never {
  throw new Error("Mnemonic returned an invalid prompt response. Reload the prompt library.");
}

export function decodePromptMetadata(value: unknown, expectedId?: string): PromptMetadata {
  const row = objectValue(value);
  if (!row || !exactKeys(row, METADATA_KEYS) || !validPromptId(row.id)
    || expectedId !== undefined && row.id !== expectedId
    || !boundedText(row.name, 200) || !boundedText(row.description, 4_000)
    || !finiteInteger(row.size_bytes, 1, 400_000) || !validRevision(row.revision)
    || !validUtcDateTime(row.created_at) || !validUtcDateTime(row.updated_at)) invalidResponse();
  return row as PromptMetadata;
}

export function decodePromptDetail(value: unknown, expectedId: string): PromptDetail {
  const row = objectValue(value);
  if (!row || !exactKeys(row, [...METADATA_KEYS, "content"])) invalidResponse();
  const { content, ...metadata } = row;
  const prompt = decodePromptMetadata(metadata, expectedId);
  if (!validPromptContent(prompt.id, content)
    || new TextEncoder().encode(content).length !== prompt.size_bytes) invalidResponse();
  return { ...prompt, content };
}

export function decodePromptLibrary(value: unknown): PromptLibraryPage {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["items", "macros"]) || !Array.isArray(row.items)
    || row.items.length !== PROMPT_IDS.length || !Array.isArray(row.macros)
    || row.macros.length === 0 || row.macros.length > 100) invalidResponse();
  const items = row.items.map((item) => decodePromptMetadata(item));
  if (new Set(items.map((item) => item.id)).size !== PROMPT_IDS.length) invalidResponse();
  const macros = row.macros.map((value) => {
    const macro = objectValue(value);
    if (!macro || !exactKeys(macro, ["macro", "description"])
      || typeof macro.macro !== "string" || !/^\$[A-Z][A-Z0-9_]{0,99}$/.test(macro.macro)
      || !boundedText(macro.description, 4_000)) invalidResponse();
    return { macro: macro.macro, description: macro.description };
  });
  if (new Set(macros.map((item) => item.macro)).size !== macros.length) invalidResponse();
  return { items, macros };
}

export function decodeRenderedPrompt(value: unknown): string {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["content"]) || !boundedText(row.content, 100_000)
    || new TextEncoder().encode(row.content).length > 400_000) invalidResponse();
  return row.content;
}

export function promptPath(projectId: string, promptId?: PromptId): string {
  return `/projects/${encodeURIComponent(projectId)}/prompts${promptId ? `/${promptId}` : ""}`;
}

export async function renderedPrompt(
  projectId: string, promptId: PromptId, workItemId: string, codeReviewId?: string
): Promise<string> {
  return decodeRenderedPrompt(await api<unknown>(`${promptPath(projectId, promptId)}/render`, {
    method: "POST",
    body: JSON.stringify({ work_item_id: workItemId, ...(codeReviewId ? { code_review_id: codeReviewId } : {}) })
  }));
}

export function promptQueryKeys(path: string, method: string): string[] | null {
  return LIST_ROUTE.test(path) && method === "GET"
    || DETAIL_ROUTE.test(path) && ["GET", "PUT"].includes(method)
    || RENDER_ROUTE.test(path) && method === "POST" ? [] : null;
}

export function isPromptRenderRoute(path: string, method: string): boolean {
  return method === "POST" && RENDER_ROUTE.test(path);
}

export function invalidPromptBody(path: string, method: string, value: unknown): string | null {
  const row = objectValue(value);
  const detail = DETAIL_ROUTE.exec(path);
  if (detail && method === "PUT" && (!row || !exactKeys(row, ["content", "expected_revision"])
    || !validRevision(row.expected_revision) || !validPromptContent(detail[1] as PromptId, row.content))) {
    return "The prompt update requires valid content and its current revision.";
  }
  if (isPromptRenderRoute(path, method) && (!row
    || Object.keys(row).some((key) => !["work_item_id", "code_review_id"].includes(key))
    || Object.hasOwn(row, "work_item_id") && !validUuid(row.work_item_id)
    || Object.hasOwn(row, "code_review_id") && !validUuid(row.code_review_id))) {
    return "The prompt render request accepts only work and review IDs.";
  }
  return null;
}

export function decodePromptResponse(path: string, method: string, value: unknown): void {
  if (LIST_ROUTE.test(path) && method === "GET") decodePromptLibrary(value);
  const detail = DETAIL_ROUTE.exec(path);
  if (detail && ["GET", "PUT"].includes(method)) decodePromptDetail(value, detail[1]);
  if (isPromptRenderRoute(path, method)) decodeRenderedPrompt(value);
}
