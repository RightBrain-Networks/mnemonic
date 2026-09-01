type SessionStorage = Pick<Storage, "getItem" | "setItem">;

type SessionDependencies = {
  storage: () => SessionStorage | null;
  uuid: () => string;
};

export function sessionIdReader(dependencies: SessionDependencies): () => string {
  let resolved = "";
  return () => {
    if (resolved) return resolved;
    const key = "mnemonic.dashboard-session";
    try {
      const storage = dependencies.storage();
      if (!storage) {
        resolved = `dashboard-${dependencies.uuid()}`;
        return resolved;
      }
      const saved = storage.getItem(key);
      if (saved) {
        resolved = saved;
        return resolved;
      }
      const created = dependencies.uuid();
      storage.setItem(key, created);
      resolved = created;
      return resolved;
    } catch {
      resolved = `dashboard-${dependencies.uuid()}`;
      return resolved;
    }
  };
}

function browserSessionUUID(): string {
  try {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  } catch {
    // Fall through to a local opaque value when Web Crypto is unavailable.
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const dashboardSessionId = sessionIdReader({
  storage: () => typeof sessionStorage === "undefined" ? null : sessionStorage,
  uuid: browserSessionUUID
});
