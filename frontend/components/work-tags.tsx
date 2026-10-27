"use client";

import { useQuery } from "@tanstack/react-query";
import { api, errorMessage } from "@/lib/api";
import { decodeTagCatalog, tagTextColor, workTagNames, type ProjectTag } from "@/lib/tags";
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

export default function WorkTags({ work }: { work: Pick<WorkItem, "project_id" | "tags" | "system_tags"> }) {
  const catalog = useProjectTags(work.project_id);
  const tags = workTagNames(work);
  if (!tags.length) return null;
  return <span className="work-tags" aria-label="Tags">{tags.map((name) => <TagBadge key={name} name={name} tag={catalog.data?.items.find((tag) => tag.name === name)} />)}</span>;
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
