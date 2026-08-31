const MAX_TIMER_DELAY_MS = 2_147_483_647;
const EXPIRED_RETRY_DELAY_MS = 65_000;

type TimerHandle = ReturnType<typeof setTimeout>;

export interface LeaseRefreshClock {
  now: () => number;
  setTimeout: (callback: () => void, delayMs: number) => TimerHandle;
  clearTimeout: (handle: TimerHandle) => void;
}

const browserClock: LeaseRefreshClock = {
  now: () => Date.now(),
  setTimeout: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
  clearTimeout: (handle) => globalThis.clearTimeout(handle)
};

export function earliestLeaseExpiry(values: readonly (string | null | undefined)[]): string | null {
  let earliest: { value: string; milliseconds: number } | null = null;
  for (const value of values) {
    if (!value) continue;
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) continue;
    if (!earliest || milliseconds < earliest.milliseconds) earliest = { value, milliseconds };
  }
  return earliest?.value ?? null;
}

export function scheduleLeaseExpiryRefresh(
  expiresAt: string,
  refresh: () => void,
  clock: LeaseRefreshClock = browserClock
): () => void {
  const expiry = Date.parse(expiresAt);
  if (!Number.isFinite(expiry)) return () => undefined;

  let cancelled = false;
  let handle: TimerHandle | null = null;
  const schedule = () => {
    if (cancelled) return;
    const remaining = expiry - clock.now();
    if (remaining <= 0) {
      handle = clock.setTimeout(() => {
        if (cancelled) return;
        refresh();
        if (!cancelled) handle = clock.setTimeout(schedule, EXPIRED_RETRY_DELAY_MS);
      }, 0);
      return;
    }
    handle = clock.setTimeout(schedule, Math.min(remaining, MAX_TIMER_DELAY_MS));
  };
  schedule();

  return () => {
    cancelled = true;
    if (handle !== null) clock.clearTimeout(handle);
  };
}
