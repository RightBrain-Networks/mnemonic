import type { CheckpointKind, MigrationOrigin } from "@/lib/types";

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
