import { ApiError } from "./api.ts";

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(signal.reason); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

/** Retry only temporary search admission, never writes or long-running search failures. */
export async function transcriptSearchRead<T>(read: () => Promise<T>, signal: AbortSignal,
  wait: (milliseconds: number, signal: AbortSignal) => Promise<void> = pause): Promise<T> {
  const delays = [500, 1500, 3500, 7000];
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try { return await read(); }
    catch (error) {
      if (signal.aborted || !(error instanceof ApiError) || error.status !== 503
        || error.code !== "transcript_search_busy" || attempt >= delays.length) throw error;
      await wait(delays[attempt], signal);
    }
  }
}
