"use client";

import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { decodeCodeReviewDetail } from "@/lib/code-reviews";
import { retainedReviewPointer, reviewDecisionStatus } from "@/lib/code-review-tasks";
import { dashboardSessionId } from "@/lib/dashboard-session";
import { decodeWorkContext } from "@/lib/duplicate-handling";
import { mutationWorkKey, useMutationIntentRegistry, useMutationScope } from "@/lib/mutation-intent";
import { decodeProjectSettings } from "@/lib/job-completion-reports";
import { renderedPrompt } from "@/lib/prompts";
import { dashboardMutationActor } from "@/lib/work-events";
import { sameUuid } from "@/lib/wire-guards";
import type { Task } from "@/lib/tasks";
import type { ManualStatusAction } from "@/lib/work-status-actions";

export function useCodeReviewActions(projectId: string, onChanged: () => void, onNotice: (message: string, error?: boolean) => void) {
  const registry = useMutationIntentRegistry();
  const scope = useMutationScope({}, registry);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const performing = useRef(false);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!copiedId) return;
    const timer = setTimeout(() => setCopiedId(null), 2500);
    return () => clearTimeout(timer);
  }, [copiedId]);

  async function changeStatus(task: Task, action: ManualStatusAction) {
    if (performing.current || registry.hasDispatched() || task.review_state !== "requested") return;
    performing.current = true;
    setBusyId(task.id);
    try {
      const base = `/projects/${projectId}/work-items/${task.work_item_id}`;
      const context = decodeWorkContext(await api<unknown>(`${base}/context?recent_limit=0&recent_event_limit=0`), projectId, task.work_item_id);
      if (context.work_item.version !== task.work_version) {
        throw new Error("This review changed since the card was loaded. Refresh before making a status decision.");
      }
      const review = context.code_review_context?.current_review;
      if (!review || !sameUuid(review.id, task.id) || review.state !== "requested") {
        throw new Error("This review episode changed. Refresh before making a status decision.");
      }
      const status = reviewDecisionStatus(action);
      if ((review.human_decision?.status ?? "to-review") === status && task.status !== "active") return;
      const terminal = ["done", "wont-do", "promoted"].includes(status);
      const settings = terminal ? decodeProjectSettings(await api<unknown>(`/projects/${projectId}/settings?work_item_id=${task.work_item_id}`), projectId) : null;
      if (!active.current || registry.hasDispatched()) return;
      await registry.execute({
        kind: "update_work", slot: `update-work:${projectId}:${task.work_item_id}`,
        projectId, conflictKeys: [mutationWorkKey(projectId, task.work_item_id)],
        method: "PATCH", path: base,
        payload: {
          expected_version: context.work_item.version, actor: dashboardMutationActor(dashboardSessionId()),
          review_decision: {
            resource_id: task.id, expected_decision_version: review.human_decision?.version ?? 0, status,
            ...(settings ? { job_completion_report: {
              summary: `A person explicitly marked the code review for “${task.title}” ${status} in the Mnemonic dashboard. This records the human decision; no agent findings or verification evidence were supplied.`,
              fyi_items: [], prompt_revision: settings.revision
            } } : {})
          }
        }
      });
      if (active.current) onNotice(status === "to-review" ? "Code review returned to Pending." : `Code review marked ${status}.`);
    } catch (error) {
      if (active.current) onNotice(errorMessage(error), true);
    } finally {
      performing.current = false;
      if (active.current) { setBusyId(null); onChanged(); }
    }
  }

  async function copyPointer(task: Task) {
    try {
      const detail = decodeCodeReviewDetail(await api<unknown>(`/projects/${projectId}/work-items/${task.work_item_id}/code-reviews/${task.id}`), projectId, task.work_item_id, task.id);
      const runnable = detail.review.state === "requested" && (detail.review.human_decision?.status ?? "to-review") === "to-review";
      const text = runnable ? (await Promise.all([
        renderedPrompt(projectId, "recall-pointer", task.work_item_id, task.id),
        renderedPrompt(projectId, "warm-code-review", task.work_item_id, task.id)
      ])).join("\n\n") : retainedReviewPointer(detail);
      if (!active.current) return;
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable.");
      await navigator.clipboard.writeText(text);
      if (active.current) { setCopiedId(task.id); onNotice("Recall pointer copied for this code review."); }
    } catch (error) {
      if (active.current) onNotice(errorMessage(error), true);
    }
  }

  return { busyId, copiedId, blocked: scope.blocked || Boolean(busyId), changeStatus, copyPointer };
}
