import { queryOptions } from "@tanstack/react-query";
import { api } from "./api.ts";
import { queryKeys } from "./query-keys.ts";
import { decodeTaskPage, TASK_PAGE_SIZE, type TaskKind, type TaskStatus } from "./tasks.ts";
import { sameUuid } from "./wire-guards.ts";

export type TaskPageFilters = {
  status: TaskStatus | "all";
  kind?: TaskKind;
  offset?: number;
  taskId?: string;
};

export function taskPageQueryOptions(projectId: string, filters: TaskPageFilters) {
  const { status, kind, offset = 0, taskId } = filters;
  return queryOptions({
    queryKey: queryKeys.taskPage(projectId, { status, kind, offset, taskId }),
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ status, limit: String(TASK_PAGE_SIZE), offset: String(offset) });
      if (kind) params.set("kind", kind);
      if (taskId) params.set("task_id", taskId);
      const value = await api<unknown>(`/projects/${projectId}/tasks?${params}`, { signal });
      const page = decodeTaskPage(value, projectId, status, kind, offset);
      if (taskId && page.items.some((task) => !sameUuid(task.id, taskId))) {
        throw new Error("Mnemonic returned a different task than requested.");
      }
      return page;
    }
  });
}
