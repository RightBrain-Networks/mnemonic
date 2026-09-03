export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

const SAFE_ERROR_CONTEXT = new Set([
  "holder_client", "expires_at", "canonical_work_item_id"
]);

const SAFE_VALIDATION_LOCATION_ROOTS = new Set(["body", "query", "path", "header", "cookie"]);
const SAFE_VALIDATION_LOCATION_PARTS = new Set([
  "actor",
  "actor_client",
  "actor_model",
  "actor_session_id",
  "body",
  "checkpoint",
  "client_operation_id",
  "context_checkpoint_id",
  "canonical_work_item_id",
  "created_by_client",
  "created_by_model",
  "created_by_session_id",
  "description",
  "direction",
  "destination_work_item_id",
  "duplicate_scope",
  "event_type",
  "gate_id",
  "gate_type",
  "expected_version",
  "id",
  "initial_checkpoint",
  "kind",
  "limit",
  "metadata",
  "name",
  "offset",
  "order",
  "priority",
  "project_id",
  "prompt",
  "q",
  "recall_pointer_template",
  "recent_event_limit",
  "recent_limit",
  "relationship_id",
  "relationship_type",
  "relationship_event_count",
  "resolution",
  "resolved_by_client",
  "resolved_by_model",
  "resolved_by_session_id",
  "reviewed_context_revision",
  "reviewed_destination_revision",
  "reviewed_source_revision",
  "repository_branch",
  "repository_url",
  "semantic",
  "slug",
  "source_client",
  "source_metadata",
  "source_model",
  "source_session_id",
  "source_session_url",
  "source_work_item_id",
  "merged_by_client",
  "merged_by_model",
  "merged_by_session_id",
  "rationale",
  "status",
  "summary",
  "tag",
  "tags",
  "target_work_item_id",
  "title",
  "type",
  "verified_against",
  "view",
  "work_item_id",
  "work_event_count",
  "work_version"
]);

function safeValidationLocation(value: unknown): string {
  if (!Array.isArray(value)) return "";
  const parts: string[] = [];
  for (const [index, part] of value.entries()) {
    if (index === 0 && typeof part === "string" && SAFE_VALIDATION_LOCATION_ROOTS.has(part)) {
      continue;
    }
    if (typeof part === "string" && SAFE_VALIDATION_LOCATION_PARTS.has(part)) {
      parts.push(part);
      continue;
    }
    if (typeof part === "number" && Number.isSafeInteger(part) && part >= 0 && parts.length) {
      parts.push(String(part));
      continue;
    }
    break;
  }
  return parts.join(".");
}

export function detailMessage(detail: unknown): { message: string; code?: string } {
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    return {
      message: detail.map((item) => {
        if (!item || typeof item !== "object") return "Invalid value";
        const issue = item as { loc?: unknown; msg?: string };
        const field = safeValidationLocation(issue.loc);
        return `${field ? `${field}: ` : ""}${issue.msg || "Invalid value"}`;
      }).join(". ")
    };
  }
  if (detail && typeof detail === "object") {
    const value = detail as { code?: unknown; message?: unknown; context?: unknown };
    const message = typeof value.message === "string"
      ? value.message
      : "The request could not be completed. Please try again.";
    const safeContext: string[] = [];
    if (value.context && typeof value.context === "object" && !Array.isArray(value.context)) {
      for (const [key, item] of Object.entries(value.context)) {
        if (SAFE_ERROR_CONTEXT.has(key) && typeof item === "string") safeContext.push(item);
      }
    }
    return {
      message: safeContext.length ? `${message} ${safeContext.join(" · ")}` : message,
      ...(typeof value.code === "string" ? { code: value.code } : {})
    };
  }
  return { message: "The request could not be completed. Please try again." };
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/mnemonic${path}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers
      }
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Cannot reach Mnemonic. Check that the application is running, then try again.", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown };
    const detail = detailMessage(payload.detail);
    throw new ApiError(detail.message, response.status, detail.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

export function isVersionConflict(error: unknown): error is ApiError {
  return error instanceof ApiError
    && (error.code === "version_conflict" || (error.status === 409 && error.code === undefined));
}

export function workItemPath(projectId: string, workItemId?: string): string {
  const base = `/projects/${encodeURIComponent(projectId)}/work-items`;
  return workItemId ? `${base}/${encodeURIComponent(workItemId)}` : base;
}
