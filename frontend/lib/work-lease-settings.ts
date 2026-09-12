import type { LeaseSettings, ProjectSettings } from "./types.ts";
import { exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

export const MAX_LEASE_MINUTES = 2_147_483_647;
export const LEASE_SETTINGS_FIELDS = [
  "lease_default_minutes", "lease_minimum_minutes", "lease_maximum_minutes"
] as const;
export type LeaseSettingsDraft = Record<typeof LEASE_SETTINGS_FIELDS[number], string>;
export type LeaseSettingsPatch = Pick<ProjectSettings, typeof LEASE_SETTINGS_FIELDS[number]>;

export function validLeaseMinutes(value: unknown): value is number {
  return finiteInteger(value, 1, MAX_LEASE_MINUTES);
}

export function validLeaseDurations(
  defaultMinutes: unknown, minimumMinutes: unknown, maximumMinutes: unknown
): boolean {
  return validLeaseMinutes(defaultMinutes) && validLeaseMinutes(minimumMinutes)
    && validLeaseMinutes(maximumMinutes)
    && minimumMinutes <= defaultMinutes && defaultMinutes <= maximumMinutes;
}

export function decodeLeaseSettings(value: unknown): LeaseSettings {
  const settings = objectValue(value);
  if (!settings || !exactKeys(settings, ["default_minutes", "minimum_minutes", "maximum_minutes"])
    || !validLeaseDurations(settings.default_minutes, settings.minimum_minutes, settings.maximum_minutes)) {
    throw new Error("Mnemonic returned invalid work lease settings.");
  }
  return settings as unknown as LeaseSettings;
}

export function leaseSettingsDraft(settings: ProjectSettings): LeaseSettingsDraft {
  return {
    lease_default_minutes: String(settings.lease_default_minutes),
    lease_minimum_minutes: String(settings.lease_minimum_minutes),
    lease_maximum_minutes: String(settings.lease_maximum_minutes)
  };
}

export function sameLeaseDraft(left: LeaseSettingsDraft, right: LeaseSettingsDraft): boolean {
  return LEASE_SETTINGS_FIELDS.every((field) => left[field] === right[field]);
}

export function parseLeaseDraft(draft: LeaseSettingsDraft): LeaseSettingsPatch | null {
  if (LEASE_SETTINGS_FIELDS.some((field) => !/^\d+$/.test(draft[field]))) return null;
  const values = {
    lease_default_minutes: Number(draft.lease_default_minutes),
    lease_minimum_minutes: Number(draft.lease_minimum_minutes),
    lease_maximum_minutes: Number(draft.lease_maximum_minutes)
  };
  return validLeaseDurations(values.lease_default_minutes, values.lease_minimum_minutes,
    values.lease_maximum_minutes) ? values : null;
}
