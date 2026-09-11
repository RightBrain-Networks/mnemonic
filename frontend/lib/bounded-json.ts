/** Bound the decoded wire body before parsing; never include raw prose in errors. */
export async function readBoundedBytes(response: Response, maximumBytes: number, signal?: AbortSignal): Promise<Uint8Array<ArrayBuffer>> {
  const declared = response.headers.get("content-length");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Mnemonic returned an empty response.");
  const chunks: Uint8Array[] = [];
  let size = 0;
  const cancel = () => { void reader.cancel(signal?.reason).catch(() => {}); };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    signal?.throwIfAborted();
    if (declared !== null && (!/^[0-9]+$/.test(declared) || Number(declared) > maximumBytes)) {
      throw new Error("Mnemonic returned an oversized response.");
    }
    while (true) {
      const { done, value } = await reader.read();
      signal?.throwIfAborted();
      if (done) break;
      size += value.byteLength;
      if (size > maximumBytes) throw new Error("Mnemonic returned an oversized response.");
      chunks.push(value);
    }
  } catch (error) {
    cancel();
    throw error;
  } finally { signal?.removeEventListener("abort", cancel); reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}
export async function readBoundedJson(response: Response, maximumBytes: number): Promise<unknown> {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(await readBoundedBytes(response, maximumBytes)));
  } catch { throw new Error("Mnemonic returned an invalid bounded response."); }
}
