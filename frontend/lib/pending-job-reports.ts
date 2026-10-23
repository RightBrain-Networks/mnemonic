import { api } from "./api.ts";
import { decodeReportPage } from "./job-completion-reports.ts";
import type { JobReportPage } from "./types.ts";

/** Freeze the entire pending queue before asking the human to dismiss it. */
export async function pendingJobReportIds(projectId: string, signal: AbortSignal): Promise<string[]> {
  const ids = new Set<string>();
  const cursors = new Set<string>();
  let previous: JobReportPage | undefined;
  for (;;) {
    signal.throwIfAborted();
    const params = new URLSearchParams({ dismissal: "undismissed", limit: "50" });
    if (previous?.next_cursor) params.set("cursor", previous.next_cursor);
    const page = decodeReportPage(await api<unknown>(
      `/projects/${encodeURIComponent(projectId)}/job-completion-reports?${params}`,
      { signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]) }
    ), projectId, { previous, limit: 50 });
    signal.throwIfAborted();
    for (const item of page.items) {
      const id = item.report.id.toLowerCase();
      if (ids.has(id)) throw new Error("Mnemonic returned incoherent report pagination.");
      ids.add(id);
    }
    if (page.next_cursor === null) return [...ids];
    if (cursors.has(page.next_cursor)) throw new Error("Mnemonic returned incoherent report pagination.");
    cursors.add(page.next_cursor);
    previous = page;
  }
}
