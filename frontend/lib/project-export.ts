import { artifactPath, decodeArtifactPage } from "./artifacts.ts";
import { backupPath, backupResponseMessage } from "./backups.ts";
import { readBoundedJson } from "./bounded-json.ts";
import { validUuid } from "./wire-guards.ts";

export async function readExportArtifacts(projectId: string, offset: number, signal: AbortSignal) {
  const response = await fetch(`${artifactPath(projectId)}?limit=100&offset=${offset}&sort=filename&order=asc&include_deleted=false`, { cache: "no-store", signal });
  const value = await readBoundedJson(response, 4 * 1024 * 1024);
  if (!response.ok) throw new Error(backupResponseMessage(value, "Unable to load project artifacts."));
  const page = decodeArtifactPage(value, projectId);
  if (page.offset !== offset || page.limit !== 100 || page.items.some((item) => item.deleted_at !== null)
    || new Set(page.items.map((item) => item.id)).size !== page.items.length) throw new Error("The artifact list could not be verified.");
  return page;
}

export function exportSelection(domainIds: string[], artifactIds: string[]) {
  if (!domainIds.length && !artifactIds.length) throw new Error("Select at least one knowledge domain or artifact.");
  if (![...domainIds, ...artifactIds].every(validUuid)
    || new Set(domainIds).size !== domainIds.length || new Set(artifactIds).size !== artifactIds.length) throw new Error("The export selection is invalid.");
  return { domain_ids: domainIds, artifact_ids: artifactIds };
}

type SavePicker = (options: { suggestedName: string }) => Promise<{ createWritable: () => Promise<{
  write: (data: Uint8Array) => Promise<void>; close: () => Promise<void>; abort: () => Promise<void>;
}> }>;

export async function downloadProjectExport(projectId: string, domainIds: string[], artifactIds: string[]) {
  const selection = exportSelection(domainIds, artifactIds);
  const filename = "selected-assets.tar.bz2";
  const picker = (window as Window & { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
  let handle: Awaited<ReturnType<SavePicker>> | undefined;
  if (picker) {
    try { handle = await picker.call(window, { suggestedName: filename }); }
    catch (error) { if (error instanceof DOMException && error.name === "AbortError") return false; throw error; }
  }
  const response = await fetch(`${backupPath(projectId)}/exports`, { method: "POST", cache: "no-store",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
  if (!response.ok) throw new Error(backupResponseMessage(await readBoundedJson(response, 4 * 1024 * 1024), "Unable to export the selected assets."));
  const length = Number(response.headers.get("content-length"));
  if (!Number.isSafeInteger(length) || length < 1) throw new Error("The export returned an invalid archive length.");
  if (handle) {
    const writable = await handle.createWritable();
    const reader = response.body?.getReader();
    let bytes = 0;
    try {
      if (!reader) throw new Error("The export did not include an archive.");
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > length) throw new Error("The export archive was incomplete. Try the export again.");
        await writable.write(value);
      }
      if (bytes !== length) throw new Error("The export archive was incomplete. Try the export again.");
      await writable.close();
    } catch (error) { await reader?.cancel().catch(() => undefined); await writable.abort().catch(() => undefined); throw error; }
    finally { reader?.releaseLock(); }
  } else {
    const blob = await response.blob();
    if (blob.size !== length) throw new Error("The export archive was incomplete. Try the export again.");
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
  return true;
}
