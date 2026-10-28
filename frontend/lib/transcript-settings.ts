import { objectValue } from "./wire-guards.ts";
import { validTranscriptDirectory } from "./transcripts.ts";
import type { TranscriptWarning } from "./transcript-health.ts";

export function formatTranscriptSize(bytes: number | null): string {
  if (bytes === null || !Number.isFinite(bytes) || bytes < 0) return "Unavailable";
  if (bytes < 1024) return `${bytes} B`;
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
  const amount = bytes / 1024 ** exponent;
  return `${new Intl.NumberFormat("en", { maximumFractionDigits: amount < 10 ? 1 : 0 }).format(amount)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`;
}

export type TranscriptFolderPage = {
  directory: string | null;
  parent: string | null;
  entries: { name: string; path: string }[];
  truncated: boolean;
};
export function decodeTranscriptFolders(value: unknown): TranscriptFolderPage {
  const row = objectValue(value);
  if (!row || !(row.directory === null || validTranscriptDirectory(row.directory))
    || !(row.parent === null || validTranscriptDirectory(row.parent))
    || typeof row.truncated !== "boolean" || !Array.isArray(row.entries) || row.entries.length > 1000
    || !row.entries.every((entry) => {
      const item = objectValue(entry);
      return item && typeof item.name === "string" && item.name.length > 0 && item.name.length <= 4096 && validTranscriptDirectory(item.path);
    })) throw new Error("Unable to read the server folders.");
  return row as unknown as TranscriptFolderPage;
}

export function warningsForTranscriptDirectory(directory: string, directories: string[], warnings: TranscriptWarning[]): TranscriptWarning[] {
  const contains = (root: string, path: string) => path === root || path.startsWith(`${root.replace(/\/+$/, "")}/`);
  return warnings.filter((warning) => {
    if (!warning.path) return false;
    const matching = directories.filter((root) => contains(root, warning.path!)).sort((a, b) => b.length - a.length);
    // A broken mounted ancestor also affects the project folders beneath it.
    return matching.length ? matching[0] === directory : contains(warning.path, directory);
  });
}
