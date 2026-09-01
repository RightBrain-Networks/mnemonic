import type { CheckpointKind, MigrationOrigin, Readiness, WorkStatus } from "@/lib/types";

export const checkpointKindLabels: Record<CheckpointKind, string> = {
  context: "Context checkpoint",
  progress: "Progress checkpoint",
  completion: "Completion checkpoint"
};

export function migrationWarning(origin: MigrationOrigin): string | null {
  if (origin === "legacy-handoff-snapshot") {
    return "Migrated snapshot: the prompt is preserved exactly, but the legacy system did not record who made later prompt edits.";
  }
  if (origin === "legacy-comment") {
    return "Migrated from the legacy append-only progress log.";
  }
  return null;
}

export function normalizedTags(value: string): string[] {
  return [...new Set(value.split(",").map((tag) => tag.trim().toLowerCase()).filter(Boolean))].slice(0, 20);
}

export function editableLifecycleStatuses(status: WorkStatus): WorkStatus[] {
  if (status === "open") return ["open", "wont-do", "promoted"];
  return [status, "open"];
}

export function readinessAfterWorkSave(
  readiness: Readiness,
  previousStatus: WorkStatus,
  nextStatus: WorkStatus
): Readiness {
  if (previousStatus === nextStatus) {
    return { ...readiness, lifecycle_status: nextStatus, is_terminal: nextStatus !== "open" };
  }
  if (nextStatus === "open") {
    return {
      ...readiness,
      lifecycle_status: "open",
      is_terminal: false,
      has_active_lease: false,
      active_lease: null,
      is_ready: !readiness.is_blocked,
      display_state: readiness.is_blocked ? "blocked" : "ready"
    };
  }
  return {
    ...readiness,
    lifecycle_status: nextStatus,
    is_terminal: true,
    has_active_lease: false,
    active_lease: null,
    is_ready: false,
    display_state: nextStatus
  };
}
