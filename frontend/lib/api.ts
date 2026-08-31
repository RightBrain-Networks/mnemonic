export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

function detailMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      if (!item || typeof item !== "object") return "Invalid value";
      const issue = item as { loc?: unknown[]; msg?: string };
      const field = issue.loc?.filter((part) => part !== "body" && part !== "query").join(".");
      return `${field ? `${field}: ` : ""}${issue.msg || "Invalid value"}`;
    }).join(". ");
  }
  return "The request could not be completed. Please try again.";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/mnemonic${path}`, {
      ...init,
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers }
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("Cannot reach Mnemonic. Check that the application is running, then try again.", 0);
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown };
    throw new ApiError(detailMessage(payload.detail), response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

export function handoffPath(projectId: string, handoffId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/handoffs/${encodeURIComponent(handoffId)}`;
}
