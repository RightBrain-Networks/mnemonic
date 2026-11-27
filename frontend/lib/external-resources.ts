import { boundedText, finiteInteger, objectValue, sameUuid, validUuid } from "./wire-guards.ts";
import { validLeaseDurations } from "./work-lease-settings.ts";

export const MAX_RESOURCE_CAPACITY = 4_294_967_295;

export type ResourceDefinition = {
  name: string;
  capacity: number;
  minimum_minutes: number;
  default_minutes: number;
  maximum_minutes: number;
};

export type ResourceLease = {
  id: string;
  resource_id: string;
  holder_client: string;
  holder_session_id: string;
  quantity: number;
  acquired_at: string;
  expires_at: string;
};

export type ExternalResource = ResourceDefinition & {
  id: string;
  project_id: string;
  available: number;
  leases: ResourceLease[];
};

function validLease(value: unknown, resourceId: string): value is ResourceLease {
  const lease = objectValue(value);
  return Boolean(lease && validUuid(lease.id) && sameUuid(lease.resource_id, resourceId)
    && boundedText(lease.holder_client, 80) && boundedText(lease.holder_session_id, 200)
    && finiteInteger(lease.quantity, 1, MAX_RESOURCE_CAPACITY)
    && typeof lease.acquired_at === "string" && Number.isFinite(Date.parse(lease.acquired_at))
    && typeof lease.expires_at === "string" && Number.isFinite(Date.parse(lease.expires_at)));
}

export function decodeResource(value: unknown, projectId: string): ExternalResource {
  const record = objectValue(value);
  if (!record || !sameUuid(record.project_id, projectId) || !validUuid(record.id)
    || !boundedText(record.name, 200) || !finiteInteger(record.capacity, 1, MAX_RESOURCE_CAPACITY)
    || !validLeaseDurations(record.default_minutes, record.minimum_minutes, record.maximum_minutes)
    || !finiteInteger(record.available, 0, record.capacity) || !Array.isArray(record.leases)) {
    throw new Error("Mnemonic returned an invalid external resource.");
  }
  const id = record.id;
  const leases = record.leases;
  if (!leases.every((lease) => validLease(lease, id))
    || new Set(leases.map((lease) => lease.id)).size !== leases.length
    || leases.reduce((total, lease) => total + lease.quantity, record.available) !== record.capacity) {
    throw new Error("Mnemonic returned inconsistent external lease quantities.");
  }
  return record as ExternalResource;
}

export function decodeResources(value: unknown, projectId: string): ExternalResource[] {
  const catalog = objectValue(value);
  if (!catalog || !sameUuid(catalog.project_id, projectId) || !Array.isArray(catalog.items)) {
    throw new Error("Mnemonic returned an invalid external resource list.");
  }
  const resources = catalog.items.map((item) => decodeResource(item, projectId));
  if (new Set(resources.map((resource) => resource.id)).size !== resources.length) {
    throw new Error("Mnemonic returned duplicate external resources.");
  }
  return resources;
}

export type ResourceDraft = { [Field in keyof ResourceDefinition]: string };

export function parseResourceDraft(draft: ResourceDraft): ResourceDefinition | null {
  const { name, ...numbers } = draft;
  if (!boundedText(name.trim(), 200) || !Object.values(numbers).every((value) => /^\d+$/.test(value))) return null;
  const result = { name: name.trim(), capacity: Number(draft.capacity), minimum_minutes: Number(draft.minimum_minutes),
    default_minutes: Number(draft.default_minutes), maximum_minutes: Number(draft.maximum_minutes) };
  return finiteInteger(result.capacity, 1, MAX_RESOURCE_CAPACITY)
    && validLeaseDurations(result.default_minutes, result.minimum_minutes, result.maximum_minutes) ? result : null;
}
