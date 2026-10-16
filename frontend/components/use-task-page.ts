"use client";

import { useCallback, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { errorMessage } from "@/lib/api";
import { type TaskKind, type TaskStatus } from "@/lib/tasks";
import { taskPageQueryOptions } from "@/lib/task-queries";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";

export function useTaskPage(projectId: string, status: TaskStatus | "all", kind?: TaskKind, offset = 0, taskId?: string, enabled = true) {
  enabled = enabled && Boolean(projectId);
  const options = taskPageQueryOptions(projectId, { status, kind, offset, taskId });
  const query = useQuery({ ...options, enabled });
  const key = JSON.stringify(options.queryKey);
  const page = enabled ? query.data ?? null : null;
  const error = enabled && query.error ? errorMessage(query.error) : "";
  const busy = enabled && query.isFetching;
  const { refetch } = query;
  const retry = useCallback(() => {
    if (enabled) void refetch({ cancelRefetch: false });
  }, [enabled, refetch]);
  const expiry = earliestLeaseExpiry([page?.next_lease_expires_at]);
  useEffect(() => enabled && expiry ? scheduleLeaseExpiryRefresh(expiry, retry) : undefined, [enabled, expiry, key, retry]);
  useFailedReadRetry({ scope: key, failed: Boolean(error), busy, retry, enabled });
  return { page, error, busy, retry };
}
