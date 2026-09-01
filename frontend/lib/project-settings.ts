import type { ProjectSettings } from "@/lib/types";

export function isBlockingProjectSettingsLoad(
  projectId: string,
  settings: ProjectSettings | null
): boolean {
  return settings?.project_id !== projectId;
}

export function isCurrentProjectSettingsLoad(
  requestGeneration: number,
  currentGeneration: number,
  aborted: boolean
): boolean {
  return !aborted && requestGeneration === currentGeneration;
}
