import { boundedText, exactKeys, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";

export interface WorkTranscriptLink {
  id: string;
  project_id: string;
  work_item_id: string;
  filename: string;
  client: string;
  kind: "primary" | "subagent";
  status: "waiting" | "pending" | "processing" | "ready" | "failed";
  last_updated_at: string | null;
  session_ids: string[];
  models: string[];
}
export interface WorkTranscriptLinks { items: WorkTranscriptLink[]; total: number; omitted_count: number }
export function decodeWorkTranscripts(value: unknown, projectId: string, workItemId: string): WorkTranscriptLinks {
  const page = objectValue(value);
  if (!page || !exactKeys(page, ["items", "total", "omitted_count"]) || !Array.isArray(page.items)
    || !finiteInteger(page.total) || !finiteInteger(page.omitted_count) || page.items.length !== Math.min(page.total, 20)
    || page.total !== page.items.length + page.omitted_count) throw new Error("Mnemonic returned invalid transcript links.");
  const items = page.items.map((value) => {
    const row = objectValue(value);
    if (!row || !exactKeys(row, ["id", "project_id", "work_item_id", "filename", "client", "kind", "status", "last_updated_at", "session_ids", "models"])
      || !validUuid(row.id) || !sameUuid(row.project_id, projectId) || !sameUuid(row.work_item_id, workItemId)
      || !boundedText(row.filename, 4096) || !boundedText(row.client, 200) || !["primary", "subagent"].includes(String(row.kind))
      || !["waiting", "pending", "processing", "ready", "failed"].includes(String(row.status))
      || !(row.last_updated_at === null || typeof row.last_updated_at === "string" && row.last_updated_at.length <= 40 && Number.isFinite(Date.parse(row.last_updated_at)))
      || ![row.session_ids, row.models].every((list) => Array.isArray(list) && list.length <= 8 && list.every((item) => boundedText(item, 200)))) throw new Error("Mnemonic returned a transcript link outside this work item.");
    return row as unknown as WorkTranscriptLink;
  });
  if (new Set(items.map((item) => item.id.toLowerCase())).size !== items.length) throw new Error("Mnemonic returned duplicate transcript links.");
  return { items, total: page.total, omitted_count: page.omitted_count };
}
