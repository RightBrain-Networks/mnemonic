import type {
  Page,
  Project,
  WorkContext,
  WorkMoveResult,
  WorkStatus,
  WorkSummary
} from "./types.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  objectValue,
  sameUuid,
  validUnicode,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

const PROJECT_CATALOG_PAGE_SIZE = 100;
const PROJECT_KEYS = [
  "id",
  "name",
  "slug",
  "description",
  "repository_url",
  "created_at",
  "updated_at"
] as const;
const PROJECT_PAGE_KEYS = ["items", "total", "limit", "offset"] as const;
const PROJECT_SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export type WorkMoveDisplayStatus = WorkStatus | "dropped";

function optionalProjectText(value: unknown, maximum: number): value is string {
  return typeof value === "string"
    && validUnicode(value)
    && !value.includes("\0")
    && Array.from(value).length <= maximum;
}

function validProjectUrl(value: unknown): value is string {
  if (
    !boundedText(value, 2_000)
    || !/^https?:\/\//i.test(value)
    || /\s/u.test(value)
  ) return false;
  try {
    const parsed = new URL(value);
    return (parsed.protocol === "http:" || parsed.protocol === "https:")
      && Boolean(parsed.hostname)
      && !parsed.username
      && !parsed.password;
  } catch {
    return false;
  }
}

function decodeProject(value: unknown): Project {
  const project = objectValue(value);
  if (
    !project
    || !exactKeys(project, PROJECT_KEYS)
    || !validUuid(project.id)
    || !boundedText(project.name, 120)
    || typeof project.slug !== "string"
    || project.slug.length > 100
    || !PROJECT_SLUG_PATTERN.test(project.slug)
    || !optionalProjectText(project.description, 4_000)
    || !(project.repository_url === null || validProjectUrl(project.repository_url))
    || !validUtcDateTime(project.created_at)
    || !validUtcDateTime(project.updated_at)
  ) {
    throw new Error("The project catalog response was invalid.");
  }
  return project as unknown as Project;
}

export function decodeProjectCatalogPage(
  value: unknown,
  expectedOffset: number
): Page<Project> {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, PROJECT_PAGE_KEYS)
    || !finiteInteger(expectedOffset)
    || !finiteInteger(page.total)
    || page.limit !== PROJECT_CATALOG_PAGE_SIZE
    || page.offset !== expectedOffset
    || !Array.isArray(page.items)
    || expectedOffset > page.total
    || page.items.length !== Math.min(
      PROJECT_CATALOG_PAGE_SIZE,
      page.total - expectedOffset
    )
  ) {
    throw new Error("The project catalog response was invalid.");
  }
  const items = page.items.map(decodeProject);
  if (
    new Set(items.map((project) => project.id.toLowerCase())).size !== items.length
  ) {
    throw new Error("The project catalog response was invalid.");
  }
  return {
    items,
    total: page.total,
    limit: page.limit,
    offset: page.offset
  };
}

export async function loadCompleteProjectCatalog(
  fetchPage: (offset: number) => Promise<unknown>,
  isCurrent: () => boolean
): Promise<Project[] | null> {
  const all: Project[] = [];
  let total: number | null = null;
  do {
    if (!isCurrent()) return null;
    const value = await fetchPage(all.length);
    if (!isCurrent()) return null;
    const page = decodeProjectCatalogPage(value, all.length);
    if (total !== null && page.total !== total) {
      throw new Error("The project catalog changed while it was being refreshed.");
    }
    total ??= page.total;
    all.push(...page.items);
    if (!sameProjectCatalog(all, all)) {
      throw new Error("The project catalog changed while it was being refreshed.");
    }
  } while (all.length < (total ?? 0));
  if (!isCurrent()) return null;
  if (total === null || all.length !== total) {
    throw new Error("The complete project catalog could not be verified.");
  }
  all.sort((left, right) =>
    left.name.localeCompare(right.name) || left.id.localeCompare(right.id)
  );
  return all;
}

export function sameProjectCatalog(
  left: readonly Pick<Project, "id">[],
  right: readonly Pick<Project, "id">[]
): boolean {
  const normalized = (projects: readonly Pick<Project, "id">[]) => {
    const ids = projects.map((project) => project.id);
    if (
      ids.some((id) => !validUuid(id))
      || new Set(ids.map((id) => id.toLowerCase())).size !== ids.length
    ) return null;
    return ids.map((id) => id.toLowerCase()).sort();
  };
  const leftIds = normalized(left);
  const rightIds = normalized(right);
  return leftIds !== null
    && rightIds !== null
    && leftIds.length === rightIds.length
    && leftIds.every((id, index) => id === rightIds[index]);
}

export function preservedWorkMoveDisplayStatus(
  context: WorkContext
): WorkMoveDisplayStatus {
  return context.readiness.has_dropped_lease ? "dropped" : context.work_item.status;
}

export function workMoveDisabledReason(
  context: WorkContext | null,
  mutationBlocked: boolean
): string | null {
  if (!context) return "Wait for the current work context before moving this item.";
  if (mutationBlocked) return "Resolve the pending mutation before moving this work item.";
  if (context.readiness.has_active_lease) {
    return "Release the active lease before moving this work item.";
  }
  if (context.duplicate_member_total > 0) {
    return "A work item in a duplicate group cannot be moved.";
  }
  if (context.relationship_counts.total > 0) {
    return "Remove this work item’s relationships before moving it.";
  }
  if (context.readiness.is_gated) {
    return "Resolve every human question before moving this work item.";
  }
  return null;
}

export function summaryAfterWorkMove(
  previous: WorkSummary | null,
  result: WorkMoveResult,
  displayStatus: WorkMoveDisplayStatus
): WorkSummary | null {
  if (
    !previous
    || !sameUuid(previous.work_item.id, result.work_item.id)
    || !sameUuid(previous.work_item.project_id, result.source_project_id)
  ) return null;
  return {
    ...previous,
    work_item: result.work_item,
    readiness: {
      ...previous.readiness,
      lifecycle_status: result.work_item.status,
      display_state: displayStatus
    }
  };
}

export async function resolveCurrentWorkProject(
  projects: readonly Pick<Project, "id">[],
  workItemId: string,
  preferredProjectId: string | null,
  probe: (projectId: string, workItemId: string) => Promise<boolean>
): Promise<string | null> {
  const projectIds = projects.map((project) => project.id);
  if (
    !validUuid(workItemId)
    || projectIds.some((projectId) => !validUuid(projectId))
    || new Set(projectIds.map((projectId) => projectId.toLowerCase())).size
      !== projectIds.length
    || preferredProjectId !== null && !validUuid(preferredProjectId)
  ) {
    throw new Error("The work-placement lookup scope is invalid.");
  }
  const preferred = preferredProjectId?.toLowerCase();
  const ordered = [
    ...projectIds.filter((projectId) => projectId.toLowerCase() === preferred),
    ...projectIds.filter((projectId) => projectId.toLowerCase() !== preferred)
  ];
  for (const projectId of ordered) {
    if (await probe(projectId, workItemId)) return projectId;
  }
  return null;
}
