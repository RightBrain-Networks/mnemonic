import { exactKeys, finiteInteger, objectValue } from "./wire-guards.ts";

export const SCORE_TYPES = ["none", "literal_presence", "postgresql_lexical", "tantivy_relevance", "hybrid_reciprocal_rank", "unified_reciprocal_rank", "semantic_reciprocal_rank", "cosine_similarity"] as const;
export type ScoreType = typeof SCORE_TYPES[number];
export type TotalKind = "lexical_matches" | "ranked_candidates" | "browsed_records";
export const SEARCH_RANKING_FIELDS = ["score_type", "total_kind", "semantic"] as const;
export const HIT_RANKING_FIELDS = ["rank", "score", "score_type"] as const;
export type SemanticDisposition = {
  inference: { status: "not_requested" | "completed" | "unavailable"; reason: "capacity_exhausted" | "deadline_exceeded" | "model_failure" | "vectors_pending" | null };
  candidate_scope: "none" | "full_scope" | "lexical_shortlist";
  partial_vectors: boolean;
  comparison_incomplete: boolean;
  retry: { max_attempts: 1; after_seconds: 1 } | null;
  cache_refresh: { status: "not_needed" | "completed" | "failed" | "queued"; reason: "cache_refresh_failed" | null };
};
export type SearchRanking = { score_type: ScoreType; total_kind: TotalKind; semantic: SemanticDisposition };
export type HitRanking = { rank: number; score: number; score_type: ScoreType };
export function validScoreType(value: unknown): value is ScoreType { return SCORE_TYPES.includes(value as ScoreType); }
export function decodeSemantic(value: unknown): SemanticDisposition {
  const row = objectValue(value), inference = objectValue(row?.inference), cache = objectValue(row?.cache_refresh), retry = objectValue(row?.retry);
  const fail = () => { throw new Error("Mnemonic returned inconsistent semantic comparison coverage."); };
  if (!row || !exactKeys(row, ["inference", "candidate_scope", "partial_vectors", "comparison_incomplete", "retry", "cache_refresh"])
    || !inference || !exactKeys(inference, ["status", "reason"]) || !["not_requested", "completed", "unavailable"].includes(String(inference.status))
    || ![null, "capacity_exhausted", "deadline_exceeded", "model_failure", "vectors_pending"].includes(inference.reason as string | null)
    || (inference.status === "unavailable") !== (inference.reason !== null)
    || !["none", "full_scope", "lexical_shortlist"].includes(String(row.candidate_scope))
    || typeof row.partial_vectors !== "boolean" || typeof row.comparison_incomplete !== "boolean"
    || !cache || !exactKeys(cache, ["status", "reason"]) || !["not_needed", "completed", "failed", "queued"].includes(String(cache.status))
    || ![null, "cache_refresh_failed"].includes(cache.reason as string | null) || (cache.status === "failed") !== (cache.reason !== null)) return fail();
  const completed = inference.status === "completed", unavailable = inference.status === "unavailable";
  if (completed !== (row.candidate_scope !== "none")
    || row.comparison_incomplete !== (unavailable || row.partial_vectors || row.candidate_scope === "lexical_shortlist")
    || (!completed && (row.partial_vectors || cache.status === "completed"))
    || (inference.status === "not_requested" && cache.status !== "not_needed")
    || (unavailable && inference.reason === "capacity_exhausted" ? !retry || !exactKeys(retry, ["max_attempts", "after_seconds"]) || retry.max_attempts !== 1 || retry.after_seconds !== 1 : row.retry !== null)) return fail();
  return row as unknown as SemanticDisposition;
}
export function decodeSearchRanking(value: unknown): SearchRanking {
  const page = objectValue(value);
  if (!page || !validScoreType(page.score_type) || !["lexical_matches", "ranked_candidates", "browsed_records"].includes(String(page.total_kind))) throw new Error("Mnemonic returned invalid search ranking.");
  const omitted = page.semantic === undefined && !["hybrid_reciprocal_rank", "semantic_reciprocal_rank"].includes(page.score_type);
  const semantic = omitted ? { inference: { status: "not_requested", reason: null }, candidate_scope: "none", partial_vectors: false, comparison_incomplete: false, retry: null, cache_refresh: { status: "not_needed", reason: null } } : page.semantic;
  return { score_type: page.score_type, total_kind: page.total_kind as TotalKind, semantic: decodeSemantic(semantic) };
}
export function decodeHitRanking(value: unknown, expected?: ScoreType, maximum?: number): HitRanking {
  const item = objectValue(value);
  if (!item || !finiteInteger(item.rank, 1) || maximum !== undefined && item.rank > maximum
    || typeof item.score !== "number" || !Number.isFinite(item.score) || item.score < 0 || !validScoreType(item.score_type)
    || expected !== undefined && item.score_type !== expected || item.score_type === "none" && item.score !== 0) throw new Error("Mnemonic returned invalid result ranking.");
  return { rank: item.rank, score: item.score, score_type: item.score_type };
}
export function comparisonNotice(semantic: SemanticDisposition): string | null {
  if (semantic.inference.reason === "vectors_pending") return semantic.cache_refresh.status === "queued"
    ? "Comparison incomplete: semantic vectors are being prepared in the background. The text results cannot rule out a duplicate; continue your work without an immediate retry."
    : "Comparison incomplete: semantic vectors are missing and background preparation could not be scheduled. The text results cannot rule out a duplicate.";
  if (semantic.inference.status === "unavailable") return "Comparison incomplete: semantic matching was unavailable. The available text results cannot rule out a duplicate." + (semantic.retry ? " You can retry once after a moment." : " An immediate retry is not recommended.");
  if (semantic.candidate_scope === "lexical_shortlist") return "Comparison incomplete: semantic matching covered only a text shortlist. Other duplicates may exist.";
  if (semantic.partial_vectors) return "Comparison incomplete: some records were unavailable for semantic matching.";
  return null;
}

export function validateSourceRanking(ranking: SearchRanking, q: string, source: "work_items" | "artifacts" | "transcripts", matchMode: string | undefined): void {
  const active = Boolean(q && matchMode), semantic = matchMode === "hybrid_lexical_semantic" || matchMode === "semantic_passages";
  const expected = !active ? "none" : matchMode === "semantic_passages" ? "semantic_reciprocal_rank" : semantic ? "hybrid_reciprocal_rank" : source === "work_items" ? "postgresql_lexical" : matchMode === "literal" ? "literal_presence" : "tantivy_relevance";
  const total = !active ? "browsed_records" : semantic ? "ranked_candidates" : "lexical_matches";
  if (ranking.score_type !== expected || ranking.total_kind !== total || ranking.semantic.inference.status !== (semantic ? "completed" : "not_requested")) throw new Error("Mnemonic returned ranking outside the requested search mode.");
}
