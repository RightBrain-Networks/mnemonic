const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type LiveSyncStatus = "connecting" | "live" | "retrying";

export type LiveSyncReady = {
  type: "ready";
  revision: number;
};

export type LiveSyncInvalidation = {
  type: "invalidate";
  revision: number;
  scope: "projects" | "work-items";
  project_id: string | null;
  work_item_id: string | null;
};

export type LiveSyncMessage = LiveSyncReady | LiveSyncInvalidation;

type BrowserLocation = Pick<Location, "host" | "protocol">;
type SocketFactory = (url: string) => WebSocket;

type LiveSyncOptions = {
  location?: BrowserLocation;
  createSocket?: SocketFactory;
  initialRetryMs?: number;
  maxRetryMs?: number;
};

function validRevision(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0;
}

function nullableUuid(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && UUID.test(value));
}

export function parseLiveSyncMessage(data: unknown): LiveSyncMessage | null {
  if (typeof data !== "string") return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const value = parsed as Record<string, unknown>;
  if (!validRevision(value.revision)) return null;
  if (value.type === "ready") {
    return { type: "ready", revision: value.revision };
  }
  if (
    value.type !== "invalidate"
    || (value.scope !== "projects" && value.scope !== "work-items")
    || !nullableUuid(value.project_id)
    || !nullableUuid(value.work_item_id)
    || (value.scope === "projects" && value.work_item_id !== null)
    || (value.scope === "work-items" && value.project_id === null)
  ) {
    return null;
  }
  return {
    type: "invalidate",
    revision: value.revision,
    scope: value.scope,
    project_id: value.project_id,
    work_item_id: value.work_item_id
  };
}

export function liveSyncUrl(
  location: BrowserLocation = window.location
): string {
  if (location.protocol !== "http:" && location.protocol !== "https:") {
    throw new Error("Live sync requires an HTTP or HTTPS dashboard.");
  }
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}/api/mnemonic/sync`;
}

export function connectLiveSync(
  onMessage: (message: LiveSyncMessage) => void,
  onStatus: (status: LiveSyncStatus) => void,
  options: LiveSyncOptions = {}
): () => void {
  const createSocket = options.createSocket ?? ((url: string) => new WebSocket(url));
  const target = liveSyncUrl(options.location);
  const initialRetryMs = options.initialRetryMs ?? 1_000;
  const maxRetryMs = options.maxRetryMs ?? 30_000;
  let retryMs = initialRetryMs;
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let socket: WebSocket | undefined;
  let stopped = false;

  function scheduleReconnect() {
    if (stopped || reconnectTimer !== undefined) return;
    onStatus("retrying");
    reconnectTimer = setTimeout(() => {
      reconnectTimer = undefined;
      connect();
    }, retryMs);
    retryMs = Math.min(retryMs * 2, maxRetryMs);
  }

  function connect() {
    if (stopped) return;
    let current: WebSocket;
    try {
      current = createSocket(target);
    } catch {
      scheduleReconnect();
      return;
    }
    socket = current;
    current.addEventListener("open", () => {
      if (stopped || socket !== current) return;
      retryMs = initialRetryMs;
    });
    current.addEventListener("message", (event) => {
      if (stopped || socket !== current) return;
      const message = parseLiveSyncMessage(event.data);
      if (!message) return;
      if (message.type === "ready") onStatus("live");
      onMessage(message);
    });
    current.addEventListener("error", () => {
      if (socket === current) current.close();
    });
    current.addEventListener("close", () => {
      if (socket !== current) return;
      socket = undefined;
      scheduleReconnect();
    });
  }

  onStatus("connecting");
  connect();
  return () => {
    stopped = true;
    if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
    socket?.close(1000, "Dashboard closed");
  };
}
