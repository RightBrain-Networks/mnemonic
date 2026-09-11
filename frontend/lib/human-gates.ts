import { decodeHumanGateRevision } from "./revision-codecs.ts";
import { decodeWorkSummary } from "./work-codecs.ts";
import type {
  HumanAttentionItem,
  HumanAttentionPage,
  HumanGatePage,
  HumanGateRead,
  HumanGateStatus
} from "@/lib/types";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  nullableBoundedText,
  objectValue,
  sameUuid,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";
const GATE_FIELDS = [
  "id", "project_id", "work_item_id", "gate_type", "question",
  "question_version", "previous_questions",
  "requested_by_client", "requested_by_session_id", "requested_by_model",
  "requested_context_revision", "created_at", "status",
  "current_context_revision", "work_changed_since_request",
  "context_checkpoint_changed_since_request", "relationships_changed_since_request",
  "context_changed_since_request", "resolved_at", "resolution", "resolved_by_client",
  "resolved_by_session_id", "resolved_by_model", "resolved_context_revision",
  "context_changed_at_resolution"
] as const;
const CURSOR_PAGE_FIELDS = ["items", "total", "limit", "next_cursor"] as const;
const HUMAN_ATTENTION_ITEM_FIELDS = ["gate", "summary"] as const;

export const HUMAN_GATE_DECODER_FIELDS = {
  GATE_FIELDS,
  "decodeCursorPage:attention": CURSOR_PAGE_FIELDS,
  "decodeCursorPage:gates": CURSOR_PAGE_FIELDS,
  "decodeHumanAttentionPage:item": HUMAN_ATTENTION_ITEM_FIELDS
} as const;

export function humanGateProjectionKey(gate: HumanGateRead): string {
  return [
    gate.question_version,
    gate.requested_context_revision.work_version,
    gate.requested_context_revision.context_checkpoint_id.toLowerCase(),
    gate.requested_context_revision.relationship_event_count,
    gate.current_context_revision.work_version,
    gate.current_context_revision.context_checkpoint_id.toLowerCase(),
    gate.current_context_revision.relationship_event_count,
    gate.work_changed_since_request ? 1 : 0,
    gate.context_checkpoint_changed_since_request ? 1 : 0,
    gate.relationships_changed_since_request ? 1 : 0,
    gate.context_changed_since_request ? 1 : 0
  ].join(":");
}

export function decodeHumanGate(
  value: unknown,
  expected?: { projectId?: string; workItemId?: string; gateId?: string; status?: HumanGateStatus }
): HumanGateRead {
  const gate = objectValue(value);
  if (
    !gate
    || !exactKeys(gate, GATE_FIELDS)
    || !validUuid(gate.id)
    || !validUuid(gate.project_id)
    || !validUuid(gate.work_item_id)
    || gate.gate_type !== "human"
    || !boundedText(gate.question, 4_000)
    || !finiteInteger(gate.question_version, 1)
    || !Array.isArray(gate.previous_questions)
    || gate.previous_questions.length !== Number(gate.question_version) - 1
    || !boundedText(gate.requested_by_client, 80)
    || !boundedText(gate.requested_by_session_id, 200)
    || !nullableBoundedText(gate.requested_by_model, 120)
    || !validUtcDateTime(gate.created_at)
    || (gate.status !== "unresolved" && gate.status !== "resolved")
    || typeof gate.work_changed_since_request !== "boolean"
    || typeof gate.context_checkpoint_changed_since_request !== "boolean"
    || typeof gate.relationships_changed_since_request !== "boolean"
    || typeof gate.context_changed_since_request !== "boolean"
    || expected?.projectId !== undefined && !sameUuid(gate.project_id, expected.projectId)
    || expected?.workItemId !== undefined && !sameUuid(gate.work_item_id, expected.workItemId)
    || expected?.gateId !== undefined && !sameUuid(gate.id, expected.gateId)
    || expected?.status !== undefined && gate.status !== expected.status
  ) throw new Error("Mnemonic returned an invalid human gate.");

  let previousTime = Date.parse(String(gate.created_at));
  for (const [index, value] of (gate.previous_questions as unknown[]).entries()) {
    const version = objectValue(value);
    if (!version || !exactKeys(version, ["version", "question", "created_at",
      "requested_by_client", "requested_by_session_id", "requested_by_model", "context_revision"])
      || version.version !== index + 1 || !boundedText(version.question, 4_000)
      || !validUtcDateTime(version.created_at) || Date.parse(version.created_at) < previousTime
      || !boundedText(version.requested_by_client, 80)
      || !boundedText(version.requested_by_session_id, 200)
      || !nullableBoundedText(version.requested_by_model, 120)) {
      throw new Error("Mnemonic returned invalid question history.");
    }
    decodeHumanGateRevision(version.context_revision);
    previousTime = Date.parse(version.created_at);
  }
  decodeHumanGateRevision(gate.requested_context_revision);
  decodeHumanGateRevision(gate.current_context_revision);

  if (gate.status === "unresolved") {
    if (
      gate.resolved_at !== null
      || gate.resolution !== null
      || gate.resolved_by_client !== null
      || gate.resolved_by_session_id !== null
      || gate.resolved_by_model !== null
      || gate.resolved_context_revision !== null
      || gate.context_changed_at_resolution !== null
    ) throw new Error("Mnemonic returned an incoherent unresolved human gate.");
  } else {
    if (
      !validUtcDateTime(gate.resolved_at)
      || !boundedText(gate.resolution, 4_000)
      || !boundedText(gate.resolved_by_client, 80)
      || !boundedText(gate.resolved_by_session_id, 200)
      || !nullableBoundedText(gate.resolved_by_model, 120)
      || typeof gate.context_changed_at_resolution !== "boolean"
    ) throw new Error("Mnemonic returned an incoherent resolved human gate.");
    decodeHumanGateRevision(gate.resolved_context_revision);
    if (Date.parse(gate.resolved_at) < Date.parse(String(gate.created_at))) {
      throw new Error("Mnemonic returned an incoherent resolved human gate.");
    }
  }
  return gate as unknown as HumanGateRead;
}


function decodeCursorPage(
  value: unknown,
  decodeItem: (item: unknown) => unknown,
  expectedLimit?: number
): { items: unknown[]; total: number; limit: number; next_cursor: string | null } {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, CURSOR_PAGE_FIELDS)
    || !Array.isArray(page.items)
    || !finiteInteger(page.total)
    || !finiteInteger(page.limit, 0, 100)
    || expectedLimit !== undefined && page.limit !== expectedLimit
    || page.items.length > Number(page.limit)
    || page.items.length > Number(page.total)
    || !(page.next_cursor === null || boundedText(page.next_cursor, 4_096))
    || page.limit === 0 && (page.items.length !== 0 || page.next_cursor !== null)
  ) throw new Error("Mnemonic returned an invalid cursor page.");
  return {
    items: page.items.map(decodeItem),
    total: page.total,
    limit: page.limit,
    next_cursor: page.next_cursor
  };
}

export function decodeHumanAttentionPage(
  value: unknown,
  projectId: string,
  options: { workItemId?: string; limit?: number } = {}
): HumanAttentionPage {
  if (!validUuid(projectId) || options.workItemId !== undefined && !validUuid(options.workItemId)) {
    throw new Error("The expected human-attention scope is invalid.");
  }
  const decoded = decodeCursorPage(value, (itemValue) => {
    const item = objectValue(itemValue);
    if (!item || !exactKeys(item, HUMAN_ATTENTION_ITEM_FIELDS)) {
      throw new Error("Mnemonic returned an invalid human-attention item.");
    }
    const gate = decodeHumanGate(item.gate, {
      projectId,
      workItemId: options.workItemId,
      status: "unresolved"
    });
    const summary = decodeWorkSummary(item.summary, projectId);
    if (
      !sameUuid(gate.work_item_id, summary.work_item.id)
      || summary.readiness.unresolved_gate_count < 1
      || !summary.readiness.is_gated
    ) throw new Error("Mnemonic returned an incoherent human-attention item.");
    return { gate, summary } satisfies HumanAttentionItem;
  }, options.limit);
  return decoded as HumanAttentionPage;
}

export function decodeHumanGatePage(
  value: unknown,
  projectId: string,
  workItemId: string,
  options: { status?: "all" | HumanGateStatus; limit?: number } = {}
): HumanGatePage {
  if (!validUuid(projectId) || !validUuid(workItemId)) {
    throw new Error("The expected human-gate history scope is invalid.");
  }
  const expectedStatus = options.status === "all" ? undefined : options.status;
  return decodeCursorPage(value, (item) => {
    const gate = decodeHumanGate(item, { workItemId, status: expectedStatus });
    if (gate.status === "unresolved" && !sameUuid(gate.project_id, projectId)) {
      throw new Error("Mnemonic returned an invalid human gate.");
    }
    return gate;
  }, options.limit) as HumanGatePage;
}

export function humanAttentionSearchParams(input: {
  workItemId?: string;
  limit?: number;
  cursor?: string | null;
} = {}): URLSearchParams {
  const params = new URLSearchParams({ limit: String(input.limit ?? 30) });
  if (input.workItemId) params.set("work_item_id", input.workItemId);
  if (input.cursor) params.set("cursor", input.cursor);
  return params;
}

export function humanGateHistorySearchParams(input: {
  status?: "all" | HumanGateStatus;
  limit?: number;
  cursor?: string | null;
} = {}): URLSearchParams {
  const params = new URLSearchParams({
    status: input.status ?? "all",
    limit: String(input.limit ?? 30)
  });
  if (input.cursor) params.set("cursor", input.cursor);
  return params;
}

export function humanGatePath(projectId: string, workItemId: string, gateId?: string): string {
  const base = `/projects/${encodeURIComponent(projectId)}/work-items/${encodeURIComponent(workItemId)}/gates`;
  return gateId ? `${base}/${encodeURIComponent(gateId)}` : base;
}

export function humanGateOmissionSentence(
  kind: "unresolved" | "resolved",
  count: number
): string | null {
  if (count <= 0) return null;
  if (kind === "unresolved") {
    return `${count} additional unresolved question${count === 1 ? " is" : "s are"} omitted from bounded recall. Use the filtered attention queue.`;
  }
  return `${count} older resolved decision${count === 1 ? " is" : "s are"} omitted from bounded recall.`;
}

export function humanGateResolutionStatus(remaining: number): string {
  if (remaining === 0) return "Answer recorded. No unresolved questions remain.";
  return `Answer recorded. ${remaining} unresolved question${remaining === 1 ? " remains" : "s remain"}.`;
}
