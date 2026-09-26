import { exactKeys, finiteInteger } from "./wire-guards.ts";

export const SEARCH_PAGINATION_FIELDS = ["next_offset", "page_truncated"] as const;
export type SearchPagination = { next_offset: number | null; page_truncated: boolean };

export function searchPageKeys(page: Record<string, unknown>, keys: readonly string[]): boolean {
  return exactKeys(page, [...keys.filter((key) => key !== "semantic" || Object.hasOwn(page, key)), ...SEARCH_PAGINATION_FIELDS]);
}

export function validSearchPagination(page: Record<string, unknown>): boolean {
  if (!Array.isArray(page.items) || !finiteInteger(page.total) || !finiteInteger(page.limit, 1, 100)
    || !finiteInteger(page.offset) || typeof page.page_truncated !== "boolean") return false;
  const count = page.items.length, expected = Math.min(page.limit, Math.max(0, page.total - page.offset));
  const end = page.offset + count;
  return (page.page_truncated ? count > 0 && count < expected : count === expected)
    && page.next_offset === (count && end < page.total ? end : null);
}

export function searchPagination(page: Record<string, unknown>): SearchPagination {
  if (!validSearchPagination(page)) throw new Error("Mnemonic returned invalid search pagination.");
  return { next_offset: page.next_offset as number | null, page_truncated: page.page_truncated as boolean };
}
