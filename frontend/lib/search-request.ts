import { boundedText, finiteInteger, objectValue, validUuid } from "./wire-guards.ts";

const facets = ["work_items", "artifacts", "transcripts"];
const sortFields = ["relevance", "created_at", "updated_at", "priority"];
function allowed(value: Record<string, unknown>, keys: string[]): boolean { return Object.keys(value).every((key) => keys.includes(key)); }
function optional(value: unknown, validate: (value: unknown) => boolean): boolean { return value === undefined || validate(value); }
function text(value: unknown, maximum: number): boolean { return boundedText(value, maximum); }
function validSort(value: unknown): boolean {
  const sort = objectValue(value);
  return Boolean(sort && allowed(sort, ["by", "direction"]) && optional(sort.by, (value) => sortFields.includes(String(value))) && optional(sort.direction, (value) => value === "asc" || value === "desc"));
}
function validFilters(value: unknown): boolean {
  const filters = objectValue(value);
  if (!filters || !allowed(filters, facets)) return false;
  return Object.entries(filters).every(([facet, value]) => {
    const filter = objectValue(value);
    if (!filter) return false;
    const keys = facet === "work_items" ? ["status", "tag", "source_client", "source_session_id", "duplicate_scope", "canonical_work_item_id", "external_url", "semantic"]
      : facet === "artifacts" ? ["work_item_id", "artifact_id", "include_deleted", "sensitive", "mime_type", "created_by_agent_session_id"]
        : ["work_item_id", "agent_session_id", "client", "kind", "status"];
    if (!allowed(filter, keys)) return false;
    return Object.entries(filter).every(([key, value]) => {
      if (value === null) return !["semantic", "include_deleted", "duplicate_scope"].includes(key) && !(facet === "work_items" && key === "status");
      if (["work_item_id", "artifact_id", "canonical_work_item_id"].includes(key)) return validUuid(value);
      if (["semantic", "include_deleted", "sensitive"].includes(key)) return typeof value === "boolean";
      if (key === "status") return (facet === "work_items" ? ["all", "pending", "active", "to-review", "dropped", "deferred", "done", "wont-do", "promoted"] : ["waiting", "pending", "processing", "ready", "failed"]).includes(String(value));
      if (key === "duplicate_scope") return ["canonical", "aliases", "all"].includes(String(value));
      if (key === "kind") return ["primary", "subagent"].includes(String(value));
      return text(value, key === "tag" ? 50 : ["client", "source_client"].includes(key) ? 80 : key === "external_url" ? 2000 : key === "mime_type" ? 255 : 200);
    });
  });
}
export function validSearchRequest(value: unknown): boolean {
  const body = objectValue(value);
  if (!body || !allowed(body, ["q", "facets", "fulltext", "filters", "sort", "facet_order", "limit", "offset"])) return false;
  if (!optional(body.q, (value) => typeof value === "string" && Array.from(value).length <= 1000 && !/[\u0000-\u001f]/u.test(value))
    || !optional(body.facets, (value) => Array.isArray(value) && value.length > 0 && value.length <= 3 && new Set(value).size === value.length && value.every((item) => facets.includes(item)))
    || !optional(body.fulltext, (value) => typeof value === "boolean") || !optional(body.filters, validFilters)
    || !optional(body.sort, validSort) || !optional(body.limit, (value) => finiteInteger(value, 1, 100))
    || !optional(body.offset, (value) => finiteInteger(value, 0, 1_000_000))) return false;
  if (body.facet_order !== undefined) {
    if (!Array.isArray(body.facet_order) || body.facet_order.length > 3) return false;
    const selected = body.facets as string[] | undefined;
    const ordered = body.facet_order.map((value) => objectValue(value));
    if (!ordered.every((order) => order && allowed(order, ["facet", "sort"]) && facets.includes(String(order.facet)) && (!selected || selected.includes(String(order.facet))) && (order.sort === null || optional(order.sort, validSort)))
      || new Set(ordered.map((order) => order?.facet)).size !== ordered.length) return false;
  }
  return true;
}
