import { nfkcUnicode15_1 } from "./title-normalization.ts";
import { validExternalCandidates, validSparseReferences, referenceKeys } from "./external-references.ts";
import type {
  DuplicateCandidateSummary,
  DuplicateSuggestion,
  DuplicateSuggestionInput,
  DuplicateSuggestionPage,
  DuplicateSuggestionSignal,
  ExternalSuggestion,
  ExternalCandidate,
  WorkStatus
} from "@/lib/types";
import { decodeWorkIdentityPointer } from "./work-codecs.ts";
import { normalizedTags } from "./work-item-view.ts";
import {
  boundedText,
  exactKeys,
  finiteInteger,
  objectValue,
  sameUuid,
  validUtcDateTime,
  validUuid
} from "./wire-guards.ts";

const CANDIDATE_FIELDS = [
  "work_item_id",
  "title",
  "summary",
  "status",
  "updated_at",
  "duplicate_member_count",
  "external_references"
] as const;
const EXTERNAL_SUGGESTION_FIELDS = ["rank", "signals", "reference"] as const;
const EXTERNAL_RESULT_REFERENCE_FIELDS = ["url", "title", "state"] as const;
const SUGGESTION_FIELDS = ["canonical_work", "matched_member", "rank", "signals"] as const;
const PAGE_FIELDS = [
  "items",
  "limit",
  "mode",
  "semantic_available",
  "semantic_scope",
  "composition_version",
  "exact_title_group_total",
  "omitted_exact_title_group_count",
  "external_items", "external_candidate_count", "external_scope"
] as const;

export const DUPLICATE_SUGGESTION_DECODER_FIELDS = {
  decodeExternalSuggestion: EXTERNAL_SUGGESTION_FIELDS,
  decodeExternalCandidateReference: EXTERNAL_RESULT_REFERENCE_FIELDS,
  decodeDuplicateCandidateSummary: CANDIDATE_FIELDS,
  "decodeDuplicateSuggestion:item": SUGGESTION_FIELDS,
  decodeDuplicateSuggestionPage: PAGE_FIELDS
} as const;

const WORK_STATUSES = new Set<WorkStatus>([
  "pending", "deferred", "done", "wont-do", "promoted"
]);
const SIGNAL_ORDER: readonly DuplicateSuggestionSignal[] = [
  "exact_title", "lexical", "semantic"
];

function storedDuplicateTitleKey(value: string): string {
  return nfkcUnicode15_1(value)
    .replace(/^[\t\n\v\f\r ]+|[\t\n\v\f\r ]+$/g, "")
    .replace(/[\t\n\v\f\r ]+/g, " ")
    .replace(/[A-Z]/g, (character) => character.toLowerCase());
}

function draftDuplicateTitleKey(value: string): string {
  // Pydantic strips Unicode White_Space from create-title boundaries before
  // PostgreSQL applies the deliberately narrower POSIX title-key contract.
  return storedDuplicateTitleKey(value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, ""));
}

export function duplicateSuggestionInputFromForm(form: FormData, candidates: ExternalCandidate[] = []): DuplicateSuggestionInput {
  if (!validExternalCandidates(candidates)) throw new Error("External records contain an invalid URL, title, body, state, or duplicate URL.");
  const tags = normalizedTags(String(form.get("tags") ?? ""));
  Object.freeze(tags);
  return Object.freeze({
    title: String(form.get("title") ?? ""),
    summary: String(form.get("summary") ?? ""),
    initial_prompt: String(form.get("prompt") ?? ""),
    tags,
    exclude_work_item_id: null,
    limit: 5,
    ...(candidates.length ? { external_candidates: Object.freeze(candidates.map((item) => Object.freeze({ ...item }))) as unknown as ExternalCandidate[] } : {})
  });
}

export function decodeDuplicateCandidateSummary(value: unknown): DuplicateCandidateSummary {
  const candidate = objectValue(value);
  if (
    !candidate
    || !validSparseReferences(candidate)
    || !exactKeys(candidate, referenceKeys(candidate, CANDIDATE_FIELDS.filter((key) => key !== "external_references")))
    || !validUuid(candidate.work_item_id)
    || !boundedText(candidate.title, 200)
    || !boundedText(candidate.summary, 1_000)
    || typeof candidate.status !== "string"
    || !WORK_STATUSES.has(candidate.status as WorkStatus)
    || !validUtcDateTime(candidate.updated_at)
    || !finiteInteger(candidate.duplicate_member_count)
  ) throw new Error("Mnemonic returned an invalid duplicate suggestion candidate.");
  return candidate as unknown as DuplicateCandidateSummary;
}

function decodeDuplicateSuggestion(
  value: unknown,
  rank: number,
  request: DuplicateSuggestionInput
): DuplicateSuggestion {
  const suggestion = objectValue(value);
  if (
    !suggestion
    || !exactKeys(suggestion, SUGGESTION_FIELDS)
    || suggestion.rank !== rank
    || !Array.isArray(suggestion.signals)
    || suggestion.signals.length < 1
    || suggestion.signals.length > SIGNAL_ORDER.length
  ) throw new Error("Mnemonic returned an invalid duplicate suggestion.");
  const signals = suggestion.signals as unknown[];
  const signalIndexes = signals.map((signal) => SIGNAL_ORDER.indexOf(
    signal as DuplicateSuggestionSignal
  ));
  if (
    signalIndexes.some((index) => index < 0)
    || signalIndexes.some((index, position) => position > 0 && index <= signalIndexes[position - 1]!)
  ) throw new Error("Mnemonic returned invalid duplicate suggestion signals.");

  const canonicalWork = decodeDuplicateCandidateSummary(suggestion.canonical_work);
  const matchedMember = decodeWorkIdentityPointer(suggestion.matched_member);
  const matchedCanonical = sameUuid(canonicalWork.work_item_id, matchedMember.id);
  if (
    matchedCanonical && (
      canonicalWork.title !== matchedMember.title
      || canonicalWork.status !== matchedMember.status
    )
    || !matchedCanonical && canonicalWork.duplicate_member_count < 1
    || request.exclude_work_item_id !== null && (
      sameUuid(canonicalWork.work_item_id, request.exclude_work_item_id)
      || sameUuid(matchedMember.id, request.exclude_work_item_id)
    )
    || signals.includes("exact_title")
      && storedDuplicateTitleKey(matchedMember.title) !== draftDuplicateTitleKey(request.title)
  ) throw new Error("Mnemonic returned an incoherent duplicate suggestion.");
  return {
    canonical_work: canonicalWork,
    matched_member: matchedMember,
    rank,
    signals: signals as DuplicateSuggestionSignal[]
  };
}

export function decodeDuplicateSuggestionPage(
  value: unknown,
  request: DuplicateSuggestionInput
): DuplicateSuggestionPage {
  const page = objectValue(value);
  if (
    !page
    || !exactKeys(page, PAGE_FIELDS.filter((key) => !key.startsWith("external_") || Boolean(request.external_candidates?.length)))
    || !Array.isArray(page.items)
    || page.limit !== request.limit
    || !finiteInteger(page.limit, 1, 10)
    || page.items.length > Number(page.limit)
    || !["hybrid_full", "hybrid_shortlist", "lexical"].includes(String(page.mode))
    || typeof page.semantic_available !== "boolean"
    || !["full_project", "lexical_shortlist", "unavailable"].includes(
      String(page.semantic_scope)
    )
    || page.composition_version !== "duplicate-suggestion-v1"
    || !finiteInteger(page.exact_title_group_total)
    || !finiteInteger(page.omitted_exact_title_group_count)
  ) throw new Error("Mnemonic returned an invalid duplicate suggestion page.");
  const semanticContract = page.mode === "lexical"
    ? !page.semantic_available && page.semantic_scope === "unavailable"
    : page.mode === "hybrid_full"
      ? page.semantic_available && page.semantic_scope === "full_project"
      : page.semantic_available && page.semantic_scope === "lexical_shortlist";
  if (!semanticContract) {
    throw new Error("Mnemonic returned an incoherent duplicate suggestion mode.");
  }

  const items = page.items.map((item, index) => (
    decodeDuplicateSuggestion(item, index + 1, request)
  ));
  const canonicalIds = items.map((item) => item.canonical_work.work_item_id.toLowerCase());
  const matchedIds = items.map((item) => item.matched_member.id.toLowerCase());
  const visibleExact = Math.min(Number(page.exact_title_group_total), Number(page.limit));
  const exactItems = items.filter((item) => item.signals.includes("exact_title"));
  const exactLaneCoherent = items.slice(0, visibleExact).every((item) => (
    item.signals.includes("exact_title")
  )) && items.slice(visibleExact).every((item) => !item.signals.includes("exact_title"));
  if (
    new Set(canonicalIds).size !== canonicalIds.length
    || new Set(matchedIds).size !== matchedIds.length
    || items.length < visibleExact
    || exactItems.length !== visibleExact
    || !exactLaneCoherent
    || Number(page.omitted_exact_title_group_count)
      !== Number(page.exact_title_group_total) - visibleExact
    || page.mode === "lexical" && items.some((item) => item.signals.includes("semantic"))
  ) throw new Error("Mnemonic returned incoherent duplicate suggestion results.");

  const external = decodeExternalResults(page, request);
  return {
    ...external,
    items,
    limit: page.limit as number,
    mode: page.mode as DuplicateSuggestionPage["mode"],
    semantic_available: page.semantic_available as boolean,
    semantic_scope: page.semantic_scope as DuplicateSuggestionPage["semantic_scope"],
    composition_version: page.composition_version as string,
    exact_title_group_total: page.exact_title_group_total as number,
    omitted_exact_title_group_count: page.omitted_exact_title_group_count as number
  };
}

function decodeExternalResults(page: Record<string, unknown>, request: DuplicateSuggestionInput): Pick<DuplicateSuggestionPage, "external_items" | "external_candidate_count" | "external_scope"> {
  const candidates = request.external_candidates ?? [];
  if (!candidates.length) return {};
  const fail = () => { throw new Error("Mnemonic returned incoherent external comparison results."); };
  if (!validExternalCandidates(candidates) || page.external_candidate_count !== candidates.length
    || !["hybrid", "lexical", "unavailable"].includes(String(page.external_scope))
    || !Array.isArray(page.external_items) || page.external_items.length > request.limit) return fail();
  const scope = page.external_scope as "hybrid" | "lexical" | "unavailable";
  if (scope === "unavailable" && page.external_items.length) return fail();
  const exact = candidates.filter((item) => storedDuplicateTitleKey(item.title) === draftDuplicateTitleKey(request.title))
    .sort((left, right) => left.url < right.url ? -1 : left.url > right.url ? 1 : 0).slice(0, request.limit);
  const seen = new Set<string>();
  const items: ExternalSuggestion[] = page.external_items.map((value, index) => {
    const item = objectValue(value);
    const reference = objectValue(item?.reference);
    if (!item || !exactKeys(item, EXTERNAL_SUGGESTION_FIELDS) || item.rank !== index + 1
      || !reference || !exactKeys(reference, EXTERNAL_RESULT_REFERENCE_FIELDS)
      || !Array.isArray(item.signals) || item.signals.length < 1) return fail();
    const candidate = candidates.find((candidate) => candidate.url === reference.url);
    const signals = item.signals as DuplicateSuggestionSignal[];
    const positions = signals.map((signal) => SIGNAL_ORDER.indexOf(signal));
    if (!candidate || candidate.title !== reference.title || candidate.state !== reference.state
      || seen.has(candidate.url) || positions.some((position, i) => position < 0 || i > 0 && position <= positions[i - 1]!)
      || scope !== "hybrid" && signals.includes("semantic")
      || signals.includes("exact_title") !== (index < exact.length)
      || index < exact.length && candidate.url !== exact[index]!.url) return fail();
    seen.add(candidate.url);
    return { rank: index + 1, signals, reference: { url: candidate.url, title: candidate.title, state: candidate.state } };
  });
  if (scope !== "unavailable" && items.length < exact.length) return fail();
  return { external_items: items, external_candidate_count: candidates.length, external_scope: scope };
}
