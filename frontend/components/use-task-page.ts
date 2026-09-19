"use client";

import { useEffect, useState } from "react";
import { sameUuid } from "@/lib/wire-guards";
import { api, errorMessage } from "@/lib/api";
import { decodeTaskPage, TASK_PAGE_SIZE, type TaskKind, type TaskPage, type TaskStatus } from "@/lib/tasks";
import { earliestLeaseExpiry, scheduleLeaseExpiryRefresh } from "@/lib/lease-refresh";
import { useFailedReadRetry } from "@/components/use-failed-read-retry";

export function useTaskPage(projectId: string, refresh: number, status: TaskStatus | "all", kind?: TaskKind, offset = 0, taskId?: string, enabled = true) {
  const [data, setData] = useState<{ key: string; page: TaskPage } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const key = JSON.stringify([projectId, status, kind, offset, taskId]);
  const page = data?.key === key ? data.page : null;
  const error = failure?.key === key ? failure.message : "";
  const retry = () => setRetryCount((value) => value + 1);
  useEffect(() => {
    if (!enabled) { setBusy(false); return; }
    const controller = new AbortController();
    setBusy(true);
    setFailure(null);
    const query = new URLSearchParams({ status, limit: String(TASK_PAGE_SIZE), offset: String(offset) });
    if (kind) query.set("kind", kind);
    if (taskId) query.set("task_id", taskId);
    api<unknown>(`/projects/${projectId}/tasks?${query}`, { signal: controller.signal })
      .then((value) => {
        const page = decodeTaskPage(value, projectId, status, kind, offset);
        if (taskId && page.items.some((task) => !sameUuid(task.id, taskId))) throw new Error("Mnemonic returned a different task than requested.");
        if (!controller.signal.aborted) setData({ key, page });
      }).catch((error) => {
        if (!controller.signal.aborted) setFailure({ key, message: errorMessage(error) });
      }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [projectId, status, kind, offset, taskId, refresh, retryCount, key, enabled]);
  const expiry = earliestLeaseExpiry([page?.next_lease_expires_at]);
  useEffect(() => expiry ? scheduleLeaseExpiryRefresh(expiry, retry) : undefined, [expiry]);
  useFailedReadRetry({ scope: key, failed: Boolean(error), busy, retry, enabled });
  return { page, error, busy, retry };
}
