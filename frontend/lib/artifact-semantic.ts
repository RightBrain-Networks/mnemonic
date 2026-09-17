import type { Artifact, ArtifactSearchMatch } from "./artifacts.ts";
import { exactKeys, finiteInteger, objectValue, sameUuid } from "./wire-guards.ts";

const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const label = (value: unknown, maximum: number): value is string => typeof value === "string" && Array.from(value).length > 0 && Array.from(value).length <= maximum;
export type ArtifactPassage = {
  passage_id: string; artifact_revision: number; text_sha256: string;
  start_offset: number; end_offset: number; model: string; chunk_config: string;
  token_count: number; token_limit: number; cosine_similarity: number;
  score_is_probability: false; score_type: "cosine_similarity";
};
export type ArtifactEmbeddingCoverage = {
  model: string; chunk_config: string; state: "ready" | "incomplete" | "unavailable";
  ready: number; pending: number; processing: number; failed: number; unavailable: number;
  withheld: number; empty: number; passages: number; truncated: number;
  total_kind: "ranked_candidates"; score_type: "semantic_reciprocal_rank";
};
const counters = ["ready", "pending", "processing", "failed", "unavailable", "withheld", "empty", "passages", "truncated"] as const;
export function decodeEmbeddingCoverage(value: unknown): ArtifactEmbeddingCoverage {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["model", "chunk_config", "state", ...counters, "total_kind", "score_type"])
    || !label(row.model, 300) || !label(row.chunk_config, 100) || !counters.every((field) => finiteInteger(row[field]))
    || row.total_kind !== "ranked_candidates" || row.score_type !== "semantic_reciprocal_rank") throw new Error("Mnemonic returned invalid artifact embedding coverage.");
  const coverage = row as ArtifactEmbeddingCoverage;
  const partial = coverage.pending + coverage.processing + coverage.failed + coverage.unavailable + coverage.withheld + coverage.truncated > 0;
  const state = coverage.ready === 0 && coverage.unavailable > 0 ? "unavailable" : partial ? "incomplete" : "ready";
  if (coverage.state !== state || coverage.truncated > coverage.ready + coverage.pending + coverage.processing + coverage.failed + coverage.unavailable + coverage.empty || coverage.passages < coverage.ready || coverage.ready === 0 && coverage.passages !== 0) throw new Error("Mnemonic returned inconsistent artifact embedding coverage.");
  return coverage;
}
export function decodeArtifactPassage(value: unknown, artifact: Artifact, coverage: ArtifactEmbeddingCoverage, snippet: string | null): ArtifactPassage {
  const row = objectValue(value);
  if (!row || !exactKeys(row, ["passage_id", "artifact_revision", "text_sha256", "start_offset", "end_offset", "model", "chunk_config", "token_count", "token_limit", "cosine_similarity", "score_is_probability", "score_type"])
    || !digest(row.passage_id) || !digest(row.text_sha256) || row.artifact_revision !== artifact.revision
    || artifact.deleted_at !== null || !artifact.content_available || artifact.extraction.status !== "ready"
    || !finiteInteger(row.start_offset, 0, 8_000_000) || !finiteInteger(row.end_offset, 1, 8_000_000)
    || row.end_offset <= row.start_offset || row.end_offset - row.start_offset > 1500
    || row.model !== coverage.model || row.chunk_config !== coverage.chunk_config
    || !finiteInteger(row.token_count, 1, 8192) || !finiteInteger(row.token_limit, 1, 8192) || row.token_count > row.token_limit
    || typeof row.cosine_similarity !== "number" || !Number.isFinite(row.cosine_similarity) || Math.abs(row.cosine_similarity) > 1
    || row.score_is_probability !== false || row.score_type !== "cosine_similarity"
    || typeof snippet !== "string" || Array.from(snippet).length !== Math.min(1000, row.end_offset - row.start_offset)) throw new Error("Mnemonic returned invalid artifact passage evidence.");
  return row as ArtifactPassage;
}
export async function verifyArtifactPassageIds(items: ArtifactSearchMatch[]): Promise<void> {
  for (const { artifact, passage } of items) {
    if (!passage) continue;
    const key = JSON.stringify([artifact.id.toLowerCase(), passage.artifact_revision, passage.text_sha256, passage.model, passage.chunk_config, passage.start_offset, passage.end_offset])
      .replace(/[\x7f-\uffff]/g, (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(key));
    const identity = Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, "0")).join("");
    if (identity !== passage.passage_id) throw new Error("Mnemonic returned passage evidence for a different source.");
  }
}
export function artifactPassageQuery(passage: ArtifactPassage): URLSearchParams {
  return new URLSearchParams({ expected_revision: String(passage.artifact_revision), expected_text_sha256: passage.text_sha256,
    offset: String(passage.start_offset), limit: String(passage.end_offset - passage.start_offset) });
}
export function decodeArtifactPassageText(value: unknown, match: ArtifactSearchMatch): string {
  const row = objectValue(value), extraction = objectValue(row?.extraction), passage = match.passage;
  if (!passage || !row || !exactKeys(row, ["project_id", "artifact_id", "revision", "sha256", "text_sha256", "extraction", "text", "offset", "limit", "total_chars", "next_offset"])
    || !sameUuid(row.project_id, match.artifact.project_id) || !sameUuid(row.artifact_id, match.artifact.id)
    || row.revision !== passage.artifact_revision || row.sha256 !== match.artifact.sha256 || row.text_sha256 !== passage.text_sha256
    || !extraction || !exactKeys(extraction, ["status", "truncated", "error_code", "extracted_at"])
    || extraction.status !== "ready" || extraction.truncated !== match.artifact.extraction.truncated || extraction.error_code !== null
    || !(extraction.extracted_at === null || typeof extraction.extracted_at === "string" && extraction.extracted_at.length <= 40 && Number.isFinite(Date.parse(extraction.extracted_at)))
    || row.offset !== passage.start_offset || row.limit !== passage.end_offset - passage.start_offset
    || !finiteInteger(row.total_chars, passage.end_offset, 8_000_000)
    || row.next_offset !== (passage.end_offset < row.total_chars ? passage.end_offset : null)
    || typeof row.text !== "string" || Array.from(row.text).length !== row.limit
    || Array.from(row.text).slice(0, 1000).join("") !== match.snippet) throw new Error("Mnemonic returned artifact text outside the selected passage.");
  return row.text;
}
