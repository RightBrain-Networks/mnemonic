"use client";

import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "@/lib/api";
import { decodeTagCatalog, tagTextColor, workTagNames, type ProjectTag, type TagMode } from "@/lib/tags";
import type { WorkItem } from "@/lib/types";

export const tagCatalogKey = (projectId: string) => ["project-tags", projectId] as const;
export function useProjectTags(projectId: string) {
  return useQuery({ queryKey: tagCatalogKey(projectId), enabled: Boolean(projectId),
    queryFn: async ({ signal }) => decodeTagCatalog(await api(`/projects/${projectId}/tags`, { signal }), projectId) });
}

export function TagBadge({ name, tag }: { name: string; tag?: ProjectTag }) {
  const color = tag?.color ?? "#c4ccd6";
  return <span className="work-tag" title={tag?.description} style={{ backgroundColor: color, color: tagTextColor(color) }}>{name}</span>;
}

export function TagList({ projectId, names }: { projectId: string; names: string[] | null }) {
  const catalog = useProjectTags(projectId);
  if (!names?.length) return null;
  return <span className="work-tags" aria-label="Tags">{names.map((name) => <TagBadge key={name} name={name} tag={catalog.data?.items.find((tag) => tag.name === name)} />)}</span>;
}

export default function WorkTags({ work }: { work: Pick<WorkItem, "project_id" | "tags" | "system_tags"> }) {
  return <TagList projectId={work.project_id} names={workTagNames(work)} />;
}

export function TagFilterStrip({ projectId, selected, mode, onChange }: { projectId: string; selected: string[]; mode: TagMode; onChange: (tags: string[], mode: TagMode) => void }) {
  const catalog = useProjectTags(projectId);
  const group = useId();
  return <div className="tag-filter-row" role="group" aria-label="Filter by tags">
    <div className="sort-group"><span className="sort-label" aria-hidden="true">Filter:</span><fieldset className="sort-control"><legend className="sr-only">Tag matching</legend><div className="sort-options">
      {(["all", "or", "and"] as TagMode[]).map((option) => <label className={`sort-option ${mode === option ? "selected" : ""}`} key={option}><input type="radio" name={group} checked={mode === option} onChange={() => onChange(option === "all" ? [] : selected, option)} /><span>{option === "all" ? "All" : option.toUpperCase()}</span></label>)}
    </div></fieldset></div>
    <div className="tag-filter-options">{catalog.data?.items.map((tag) => <button type="button" key={tag.id} className={`tag-filter-button ${selected.includes(tag.name) ? "selected" : ""}`} aria-pressed={selected.includes(tag.name)} title={tag.description} onClick={() => {
      const next = selected.includes(tag.name) ? selected.filter((name) => name !== tag.name) : [...selected, tag.name];
      onChange(next, next.length ? mode === "all" ? "or" : mode : "all");
    }}><TagBadge name={tag.name} tag={tag} /></button>)}</div>
    {catalog.isPending && <span role="status" className="field-hint">Loading tags…</span>}
    {catalog.isError && <button type="button" className="text-link" onClick={() => void catalog.refetch()}>Retry loading tags</button>}
  </div>;
}

export function TagSelector({ projectId, value, onChange, disabled = false }: { projectId: string; value: string[]; onChange: (tags: string[]) => void; disabled?: boolean }) {
  const catalog = useProjectTags(projectId);
  return <fieldset className="tag-selector" disabled={disabled}><legend>Tags</legend>
    {catalog.isPending ? <p role="status">Loading tags…</p> : catalog.error ? <p className="error-notice" role="alert">{errorMessage(catalog.error)} <button type="button" className="text-link" onClick={() => void catalog.refetch()}>Retry</button></p> : <>
      <div className="tag-selector-options">{catalog.data?.items.map((tag) => <label key={tag.id} className={`tag-selector-option ${value.includes(tag.name) ? "selected" : ""}`} title={tag.description}>
        <input type="checkbox" checked={value.includes(tag.name)} onChange={(event) => onChange(event.target.checked ? [...value, tag.name] : value.filter((name) => name !== tag.name))} />
        <TagBadge name={tag.name} tag={tag} /><span className="tag-description">{tag.description}</span>
      </label>)}</div>
      {!value.length && <p className="field-hint">No tags selected.</p>}
    </>}
  </fieldset>;
}
