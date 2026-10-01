import { validUuid } from "./wire-guards.ts";

export const MAX_PINNED_WORK = 100;
export const pinnedWorkKey = (projectId: string) => `mnemonic.pinned-work:${projectId}`;

export function decodePinnedWork(value: string | null): string[] {
  try {
    const ids: unknown = JSON.parse(value ?? "[]");
    return Array.isArray(ids)
      ? [...new Set(ids.filter(validUuid).map((id) => id.toLowerCase()))].slice(0, MAX_PINNED_WORK)
      : [];
  } catch { return []; }
}
