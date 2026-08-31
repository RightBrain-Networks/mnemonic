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

const SAFE_ERROR_CONTEXT = new Set(["holder_client", "expires_at"]);

export function detailMessage(detail: unknown): { message: string; code?: string } {
  if (typeof detail === "string") return { message: detail };
  if (Array.isArray(detail)) {
    return {
      message: detail.map((item) => {
        if (!item || typeof item !== "object") return "Invalid value";
        const issue = item as { loc?: unknown[]; msg?: string };
        const field = issue.loc?.filter((part) => part !== "body" && part !== "query").join(".");
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

export function workItemPath(projectId: string, workItemId?: string): string {
  const base = `/projects/${encodeURIComponent(projectId)}/work-items`;
  return workItemId ? `${base}/${encodeURIComponent(workItemId)}` : base;
}

// Retained for compatibility-only callers during the canonical cutover.
export function handoffPath(projectId: string, handoffId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/handoffs/${encodeURIComponent(handoffId)}`;
}
