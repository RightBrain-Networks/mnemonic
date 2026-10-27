import { boundedText, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";
import type { WorkItem } from "./types.ts";

export type ProjectTag = {
  id: string;
  project_id: string;
  name: string;
  description: string;
  color: string;
  menu_order: number;
  system: boolean;
};
export type TagCatalog = { project_id: string; items: ProjectTag[]; total: number };
export type TagMode = "all" | "or" | "and";

export const tagPalette = ["#a8c8e8", "#b8d6be", "#d6b8df", "#e3c29f", "#a9d5d3", "#dcb8c5", "#c9c5e6", "#d7d39e"] as const;
export const randomTagColor = () => tagPalette[Math.floor(Math.random() * tagPalette.length)];
export const normalizeTagName = (name: string) => name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
export const validTagNames = (value: unknown): value is string[] => Array.isArray(value) && value.every((name) => typeof name === "string" && name.length <= 50 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) && new Set(value).size === value.length;
export const workTagNames = (work: Pick<WorkItem, "tags" | "system_tags">): string[] => [...new Set([...(work.tags ?? []), ...(work.system_tags ?? [])])];

export function tagTextColor(color: string): string {
  const channels = color.slice(1).match(/.{2}/g)?.map((part) => parseInt(part, 16) / 255) ?? [1, 1, 1];
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const luminance = 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
  return (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) ? "#000000" : "#ffffff";
}

export function decodeTagCatalog(value: unknown, projectId: string): TagCatalog {
  const page = objectValue(value);
  if (!page || !sameUuid(page.project_id, projectId) || !Array.isArray(page.items) || !finiteInteger(page.total) || page.total !== page.items.length) throw new Error("Mnemonic returned an invalid tag catalog.");
  const ids = new Set<string>(), names = new Set<string>();
  const items = page.items.map((value) => {
    const tag = objectValue(value);
    if (!tag || !validUuid(tag.id) || !sameUuid(tag.project_id, projectId) || !validTagNames([tag.name]) || !(tag.description === "" || boundedText(tag.description, 240)) || typeof tag.color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(tag.color) || !finiteInteger(tag.menu_order) || typeof tag.system !== "boolean" || ids.has(tag.id) || names.has(String(tag.name))) throw new Error("Mnemonic returned an invalid tag.");
    ids.add(tag.id); names.add(String(tag.name));
    return tag as unknown as ProjectTag;
  });
  return { project_id: projectId, items: items.sort((a, b) => a.menu_order - b.menu_order || a.name.localeCompare(b.name)), total: items.length };
}

export function assertTagMutationResponse(catalog: TagCatalog, method: string, path: string, payload: unknown): void {
  const fields = objectValue(payload);
  const target = path.split("/").at(-1);
  const mismatch = () => { throw new Error("Mnemonic returned a tag catalog that did not match the saved action."); };
  if (!fields) return mismatch();
  if (method === "DELETE") {
    if (!validUuid(target) || catalog.items.some((tag) => tag.id === target)) mismatch();
    return;
  }
  if (method === "PUT") {
    if (target !== "order" || !Array.isArray(fields.tag_ids) || JSON.stringify(catalog.items.map((tag) => tag.id)) !== JSON.stringify(fields.tag_ids)) mismatch();
    return;
  }
  const name = typeof fields.name === "string" ? normalizeTagName(fields.name) : undefined;
  const row = method === "POST" && target === "tags"
    ? catalog.items.find((tag) => tag.name === name)
    : method === "PATCH" && validUuid(target) ? catalog.items.find((tag) => tag.id === target) : undefined;
  if (!row || row.system || name !== undefined && row.name !== name
    || typeof fields.description === "string" && row.description !== fields.description.trim()
    || typeof fields.color === "string" && row.color.toLowerCase() !== fields.color.toLowerCase()) mismatch();
}
