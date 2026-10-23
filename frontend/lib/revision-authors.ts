import { finiteInteger, objectValue, validUuid } from "./wire-guards.ts";

export type RevisionAuthorWork = { work_item_id: string; title: string; holder_client: string; purpose: "implementation" | "code_review"; expires_at: string };
export type RevisionAuthorContext = {
  reference: string; author_kind: "human" | "agent"; agent_session_id: string | null; model_id: string | null;
  authored_at: string; work_observed_at: string; active_work_items: RevisionAuthorWork[]; active_work_items_truncated: boolean;
};
function timestamp(value: unknown): value is string { return typeof value === "string" && Number.isFinite(Date.parse(value)); }
export function validRevisionAuthor(value: unknown, reference: string): value is RevisionAuthorContext {
  const author = objectValue(value);
  if (!author || author.reference !== reference || !["human", "agent"].includes(String(author.author_kind))
    || !(author.agent_session_id === null || typeof author.agent_session_id === "string")
    || !(author.model_id === null || typeof author.model_id === "string")
    || !timestamp(author.authored_at) || !timestamp(author.work_observed_at)
    || !Array.isArray(author.active_work_items) || !finiteInteger(author.active_work_items.length, 0, 10)
    || typeof author.active_work_items_truncated !== "boolean") return false;
  const seen = new Set<string>();
  for (const value of author.active_work_items) {
    const work = objectValue(value);
    if (!work || !validUuid(work.work_item_id) || seen.has(work.work_item_id.toLowerCase())
      || typeof work.title !== "string" || typeof work.holder_client !== "string"
      || !["implementation", "code_review"].includes(String(work.purpose)) || !timestamp(work.expires_at)
      || Date.parse(work.expires_at) <= Date.parse(author.work_observed_at)) return false;
    seen.add(work.work_item_id.toLowerCase());
  }
  return author.author_kind !== "human" || !author.agent_session_id && !author.model_id && !seen.size;
}
