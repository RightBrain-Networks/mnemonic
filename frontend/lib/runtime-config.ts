export type RuntimeConfig = {
  timezone: string;
  work_summary_max_chars: number;
  artifact_max_bytes: number;
  backup_max_bytes: number;
};

export function decodeRuntimeConfig(value: unknown): RuntimeConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("The dashboard settings response is invalid.");
  }
  const config = value as Record<string, unknown>;
  const expected = ["timezone", "work_summary_max_chars", "artifact_max_bytes", "backup_max_bytes"];
  if (Object.keys(config).length !== expected.length || expected.some((key) => !(key in config))) {
    throw new Error("The dashboard settings response is invalid.");
  }
  if (typeof config.timezone !== "string" || !config.timezone.trim()) {
    throw new Error("The dashboard timezone is invalid.");
  }
  try { new Intl.DateTimeFormat("en", { timeZone: config.timezone }); }
  catch { throw new Error("The dashboard timezone is invalid."); }
  for (const [key, minimum] of [
    ["work_summary_max_chars", 1], ["artifact_max_bytes", 0], ["backup_max_bytes", 1]
  ] as const) {
    const maximum = config[key];
    if (typeof maximum !== "number" || !Number.isSafeInteger(maximum) || maximum < minimum) {
      throw new Error("The dashboard size limits are invalid.");
    }
  }
  if ((config.artifact_max_bytes as number) > 1_073_741_824) {
    throw new Error("The dashboard artifact size limit is invalid.");
  }
  return {
    timezone: config.timezone,
    work_summary_max_chars: config.work_summary_max_chars as number,
    artifact_max_bytes: config.artifact_max_bytes as number,
    backup_max_bytes: config.backup_max_bytes as number
  };
}

export async function loadRuntimeConfig(signal?: AbortSignal): Promise<RuntimeConfig> {
  const response = await fetch("/api/mnemonic/runtime-config", {
    cache: "no-store", credentials: "same-origin", signal,
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error("The dashboard settings could not be loaded.");
  return decodeRuntimeConfig(await response.json());
}
